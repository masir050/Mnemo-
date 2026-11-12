"""冲突消解。

同一 slot（实体::谓词）下可能同时存在多条「有效」断言——典型来源是群聊和工单
在不同时间说了不一致的话，而系统没有足够信号判断谁对。这里的策略是：
不猜，按时间窗 + 置信度给出「现值 + 并列候选 + 变更轨迹」，并把不确定性暴露在输出里。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Assertion, Candidate, parse_iso
from .retrieve import recency_of


@dataclass
class ResolvedFact:
    entity_name: str
    predicate: str
    slot: str
    current: Assertion | None = None
    alternates: list[Assertion] = field(default_factory=list)
    history: list[Assertion] = field(default_factory=list)
    confidence: float = 0.0
    conflict: bool = False
    note: str = ""
    best_score: float = 0.0

    def to_dict(self) -> dict:
        def brief(a: Assertion) -> dict:
            return {
                "id": a.id, "value": a.value, "status": a.status,
                "confidence": a.confidence, "valid_from": a.valid_from,
                "valid_to": a.valid_to, "source": f"{a.source_kind}:{a.source_message_id}",
                "source_quote": a.source_quote, "pinned": a.pinned,
            }

        return {
            "entity": self.entity_name,
            "predicate": self.predicate,
            "slot": self.slot,
            "current": brief(self.current) if self.current else None,
            "alternates": [brief(a) for a in self.alternates],
            "history": [brief(a) for a in self.history],
            "confidence": round(self.confidence, 3),
            "conflict": self.conflict,
            "note": self.note,
        }


def resolve_candidates(
    store,
    candidates: list[Candidate],
    now: datetime | None = None,
    max_slots: int = 24,
) -> list[ResolvedFact]:
    """把候选断言按 slot 聚合，逐 slot 判定现值。"""
    now = now or datetime.now(timezone.utc)
    settings = store.settings

    order: list[str] = []
    grouped: dict[str, list[Candidate]] = {}
    for c in candidates:
        slot = c.assertion.slot
        if slot not in grouped:
            grouped[slot] = []
            order.append(slot)
        grouped[slot].append(c)

    facts: list[ResolvedFact] = []
    for slot in order[:max_slots]:
        items = grouped[slot]
        live = [c for c in items if c.assertion.status in ("active", "disputed")]
        dead = [c for c in items if c.assertion.status in ("superseded", "expired")]

        # 有效期已过的 active 视为失效（惰性判定，不依赖 sweep 是否跑过）
        still_live = []
        for c in live:
            vt = parse_iso(c.assertion.valid_to)
            if vt and vt < now:
                dead.append(c)
            else:
                still_live.append(c)
        live = still_live

        fact = ResolvedFact(
            entity_name=items[0].assertion.entity_name,
            predicate=items[0].assertion.predicate,
            slot=slot,
            best_score=max(c.score for c in items),
        )
        history = sorted(dead, key=lambda c: c.assertion.valid_from or c.assertion.created_at or "")
        fact.history = [c.assertion for c in history]

        if not live:
            # 没有有效值：退回链上最新一条，明确标注「已失效」
            if history:
                latest = history[-1].assertion
                fact.current = latest
                fact.confidence = latest.confidence * 0.5
                fact.note = "无现值，链上最新一条已失效"
            facts.append(fact)
            continue

        live.sort(key=lambda c: (c.assertion.confidence, recency_of(c.assertion, settings)), reverse=True)
        top = live[0]
        fact.current = top.assertion
        fact.confidence = top.assertion.confidence

        distinct = [c for c in live[1:] if c.assertion.value != top.assertion.value]
        if distinct:
            fact.conflict = True
            fact.alternates = [c.assertion for c in distinct]
            # 两个值置信度接近时，把现值置信度往下压，避免下游把并列当成定论
            if abs(distinct[0].assertion.confidence - top.assertion.confidence) < 0.12:
                fact.confidence = round(top.assertion.confidence * 0.85, 3)
                fact.note = "多来源给出不同取值，置信度接近，建议人工确认"
            else:
                fact.note = "存在其他来源的不同取值，已按置信度择优"
        elif top.assertion.status == "disputed":
            # 唯一活着的值却还挂着 disputed：说明对立面已经被否决或取代，
            # 争议事实上结束了。这时候再让人确认一遍是白问，顺手落回 active。
            if len({c.assertion.value for c in live}) == 1:
                store._set_status(top.assertion, "active", "对立值已被否决，自动恢复 active")
                fact.note = "此前的分歧已由人工裁决"
            else:
                fact.conflict = True
                fact.note = "该值处于 disputed 状态，等待人工裁决"

        facts.append(fact)

    # 保持检索阶段的相关度顺序，而不是按置信度重排：
    # 用户问的是「支付网关」，就不该让一条高分噪声（另一个实体的低置信记忆）插到前面。
    facts.sort(key=lambda f: -f.best_score)
    return facts


def render_fact(fact: ResolvedFact) -> str:
    """一行人类可读的结论，含变更轨迹摘要。"""
    if not fact.current:
        return f"{fact.entity_name} 的{fact.predicate}：无记录"
    line = f"{fact.entity_name} 的{fact.predicate}是 {fact.current.value}（置信 {fact.confidence:.2f}）"
    if fact.conflict and fact.alternates:
        alt = "、".join(f"{a.value}({a.confidence:.2f})" for a in fact.alternates)
        line += f"；另有不同记录：{alt}"
    if fact.history:
        traj = " → ".join(f"{a.value}@{a.valid_from[:10] if a.valid_from else '?'}" for a in fact.history)
        line += f"；变更轨迹：{traj} → {fact.current.value}"
    return line
