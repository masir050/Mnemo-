"""上下文组装：把候选记忆压进 token 预算。

三条规则决定了「注入量能压到全量拼接的两成以内」：
1. 同一 slot 只注入现值，历史版本压成一行轨迹，不重复占预算；
2. 按实体分组渲染——实体名只出现一次，不再逐条重复「支付网关」「支付网关」；
3. 冲突项优先占位（它最影响判断，且通常只有一两行），剩余预算贪心填充，
   装不下的直接丢弃并记账（dropped），不静默截断。

渲染里刻意不带完整来源串：来源通过 API 的 citations / 面板回溯，
不需要每行都占 8 个 token。冲突行例外——那种行必须能一眼看出是谁说的。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .conflict import ResolvedFact
from .models import Assertion, Candidate
from .retrieve import predicate_label
from .tokens import PER_MEMORY_OVERHEAD, estimate_tokens


@dataclass
class ContextBundle:
    lines: list[str] = field(default_factory=list)
    facts: list[ResolvedFact] = field(default_factory=list)
    tokens_used: float = 0.0
    budget: int = 900
    included: list[Assertion] = field(default_factory=list)
    dropped: list[Assertion] = field(default_factory=list)
    redundant_collapsed: int = 0

    @property
    def utilization(self) -> float:
        return round(self.tokens_used / max(1, self.budget), 3)

    def text(self) -> str:
        return "\n".join(self.lines)

    def as_dict(self) -> dict:
        return {
            "tokens_used": round(self.tokens_used, 1),
            "budget": self.budget,
            "utilization": self.utilization,
            "included": len(self.included),
            "dropped": len(self.dropped),
            "collapsed": self.redundant_collapsed,
            "text": self.text(),
        }


def _fact_line(fact: ResolvedFact) -> str:
    cur = fact.current
    assert cur is not None
    label = predicate_label(fact.predicate)
    line = f"  - {label}：{cur.value}（{fact.confidence:.2f}）"
    if fact.conflict and fact.alternates:
        alts = "、".join(f"{a.value}({a.confidence:.2f},{a.source_kind})" for a in fact.alternates)
        line += f"  ⚠ 另有 {alts}，需确认"
    return line


def _history_line(fact: ResolvedFact) -> str:
    if not fact.history or fact.current is None:
        return ""
    seq = " → ".join(f"{a.value}" for a in fact.history)
    return f"    ↺ 变更：{seq} → {fact.current.value}"


def assemble(
    candidates: list[Candidate],
    facts: list[ResolvedFact],
    budget: int,
    min_keep: int = 3,
) -> ContextBundle:
    bundle = ContextBundle(budget=budget)
    header = f"## 记忆上下文（预算 {budget} tok）"
    bundle.lines.append(header)
    used = estimate_tokens(header) + 2

    # --- 1. 按实体分组，相关度高的组先整组装入（组内冲突行天然排在前面）---
    groups: dict[str, list[ResolvedFact]] = {}
    order: list[str] = []
    for fact in facts:
        if not fact.current:
            continue
        if fact.entity_name not in groups:
            groups[fact.entity_name] = []
            order.append(fact.entity_name)
        groups[fact.entity_name].append(fact)

    order.sort(key=lambda e: -max(f.best_score for f in groups[e]))

    for ent in order:
        items = groups[ent]
        head_cost = estimate_tokens(f"### {ent}") + 2
        pending: list[str] = [f"### {ent}"]
        cost = head_cost

        for fact in items:
            line = _fact_line(fact)
            line_cost = estimate_tokens(line) + PER_MEMORY_OVERHEAD
            if used + cost + line_cost > budget:
                bundle.dropped.append(fact.current)  # type: ignore[arg-type]
                continue
            pending.append(line)
            cost += line_cost
            bundle.included.append(fact.current)  # type: ignore[arg-type]

            if fact.history:
                h = _history_line(fact)
                h_cost = estimate_tokens(h) + 2
                if used + cost + h_cost <= budget * 0.92:
                    pending.append(h)
                    cost += h_cost
                else:
                    bundle.redundant_collapsed += 1

        if len(pending) == 1:
            continue
        bundle.lines.extend(pending)
        used += cost

    # --- 2. 兜底：完全没有 slot 聚合结果时（如首次冷启动），退回按分数取候选 ---
    if not groups and candidates:
        for c in candidates:
            if used >= budget and len(bundle.included) >= min_keep:
                bundle.dropped.append(c.assertion)
                continue
            line = f"  - [{c.assertion.entity_name}] {predicate_label(c.assertion.predicate)}：{c.assertion.value}"
            cost = estimate_tokens(line) + PER_MEMORY_OVERHEAD
            if used + cost > budget and len(bundle.included) >= min_keep:
                bundle.dropped.append(c.assertion)
                continue
            bundle.lines.append(line)
            bundle.included.append(c.assertion)
            used += cost

    bundle.facts = facts
    bundle.tokens_used = used
    return bundle
