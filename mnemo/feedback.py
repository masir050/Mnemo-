"""纠错闭环：一次修正回写三样东西 —— pinned 断言、负例约束、泛化正则。"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field

from .config import Settings
from .models import Assertion, now_iso
from .store import Store


@dataclass
class CorrectionOutcome:
    assertion_id: str
    action: str
    new_value: str = ""
    learned_rule: dict | None = None
    negative_added: bool = False
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "assertion_id": self.assertion_id,
            "action": self.action,
            "new_value": self.new_value,
            "learned_rule": self.learned_rule,
            "negative_added": self.negative_added,
            "messages": self.messages,
        }


def _norm_quote(q: str) -> str:
    """原文比对前去掉空白。"""
    return re.sub(r"\s+", "", (q or "").strip())


def _rid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _generalize_pattern(quote: str, old_value: str, entity_name: str) -> tuple[str, dict] | None:
    """把原文里 old_value 的位置换成 (?P<val>...) 得到泛化正则；old_value 不在原文里则退化为记忆规则。"""
    quote = quote.strip()
    if not quote:
        return None

    if old_value and old_value in quote:
        prefix, _, suffix = quote.partition(old_value)
        pattern = re.escape(prefix) + r"(?P<val>[^，。；\s]{1,32})" + re.escape(suffix)
        template = {
            "kind": "generalized",
            "fixed_entity": entity_name,
            "entity_required": False,
            "origin_quote": quote,
        }
        return pattern, template

    template = {
        "kind": "memo",
        "fixed_entity": entity_name,
        "entity_required": False,
        "origin_quote": quote,
    }
    return re.escape(quote), template


class FeedbackLoop:
    def __init__(self, store: Store, settings: Settings | None = None):
        self.store = store
        self.settings = settings or store.settings

    # ---------------------------------------------------------------- 纠正
    def correct(self, assertion_id: str, new_value: str, operator: str = "panel") -> CorrectionOutcome:
        a = self.store.get_assertion(assertion_id)
        if a is None:
            raise KeyError(f"assertion not found: {assertion_id}")

        new_value = new_value.strip()
        if not new_value:
            raise ValueError("new_value 不能为空")

        # 同值 = 确认，不记负例
        if new_value == a.value:
            return self.confirm(assertion_id, operator)

        manual = Assertion(
            id=f"a_{uuid.uuid4().hex[:12]}",
            entity_id=a.entity_id,
            entity_name=a.entity_name,
            predicate=a.predicate,
            value=new_value,
            slot=a.slot,
            source_message_id=a.source_message_id,
            source_kind="manual",
            source_quote=a.source_quote,
            confidence=1.0,
            status="active",
            valid_from=now_iso(),
            extractor=f"manual:{operator}",
            pinned=True,
            note=f"人工修正，原值 {a.value}",
        )
        saved, action = self.store.write_assertion(manual)

        # 负例约束：让同类错误不再被规则抽出来
        neg = self._negate(a.entity_id, a.entity_name, a.predicate, a.value, a.source_quote)

        # 泛化规则
        rule_row = None
        derived = _generalize_pattern(a.source_quote, a.value, a.entity_name) if a.source_quote else None
        if derived:
            pattern, template = derived
            template["wrong_value"] = a.value
            if template["kind"] == "memo":
                template["fixed_value"] = new_value
            rule_id = self._save_rule(
                name=f"fix:{a.entity_name}.{a.predicate}",
                pattern=pattern,
                predicate=a.predicate,
                template=template,
                confidence=0.93 if template["kind"] == "generalized" else 0.88,
                origin=f"correction:{assertion_id}",
            )
            rule_row = {"id": rule_id, "pattern": pattern, "kind": template["kind"]}

        self.store.log_correction(
            assertion_id=a.id, action="correct", entity_id=a.entity_id, predicate=a.predicate,
            old_value=a.value, new_value=new_value, operator=operator,
            applied_rule=(rule_row or {}).get("id", ""),
        )

        msgs = [f"现值更新为 {new_value}"]
        if neg:
            msgs.append("已加入负例约束")
        if rule_row:
            msgs.append(f"已回写规则 {rule_row['id']}（{rule_row['kind']}）")
        return CorrectionOutcome(saved.id, "correct", new_value, rule_row, neg, msgs)

    def reject(self, assertion_id: str, reason: str = "", operator: str = "panel") -> CorrectionOutcome:
        a = self.store.get_assertion(assertion_id)
        if a is None:
            raise KeyError(f"assertion not found: {assertion_id}")
        a.status = "superseded"
        a.confidence = round(max(0.05, a.confidence * 0.3), 3)
        a.note = f"人工判定为误抽：{reason}".strip("：")
        a.updated_at = now_iso()
        self.store._save(a)
        neg = self._negate(a.entity_id, a.entity_name, a.predicate, a.value, a.source_quote)
        self.store.log_correction(
            assertion_id=a.id, action="reject", entity_id=a.entity_id, predicate=a.predicate,
            old_value=a.value, new_value="", operator=operator,
        )
        return CorrectionOutcome(a.id, "reject", "", None, neg, ["已标记为误抽并加入负例"])

    def confirm(self, assertion_id: str, operator: str = "panel") -> CorrectionOutcome:
        a = self.store.get_assertion(assertion_id)
        if a is None:
            raise KeyError(f"assertion not found: {assertion_id}")
        a.pinned = True
        a.confidence = 1.0
        a.status = "active"
        a.updated_at = now_iso()
        a.note = (a.note + f" | 人工确认({operator})").strip(" |")
        self.store._save(a)
        self.store.log_correction(
            assertion_id=a.id, action="confirm", entity_id=a.entity_id, predicate=a.predicate,
            old_value=a.value, new_value=a.value, operator=operator,
        )
        return CorrectionOutcome(a.id, "confirm", a.value, None, False, ["已确认，置信度置 1.0"])

    # ---------------------------------------------------------------- 约束与规则
    def _negate(self, entity_id: str, entity_name: str, predicate: str,
                value: str, quote: str = "") -> bool:
        """记一条负例：约束绑定原句，不按值永久拉黑。"""
        qn = _norm_quote(quote)
        for row in self.store.conn.execute(
            "SELECT id, template FROM learned_rule WHERE predicate = ? AND active = 1", (predicate,)
        ):
            try:
                t = json.loads(row["template"] or "{}")
            except json.JSONDecodeError:
                continue
            if t.get("kind") != "negative":
                continue
            if t.get("value") == value and _norm_quote(t.get("quote", "")) == qn:
                self.store.conn.execute(
                    "UPDATE learned_rule SET hits = hits + 1 WHERE id = ?", (row["id"],)
                )
                self.store.conn.commit()
                return True

        template = {
            "kind": "negative", "entity_id": entity_id, "entity": entity_name,
            "value": value, "quote": qn,
        }
        self.store.conn.execute(
            """INSERT INTO learned_rule (id, name, pattern, predicate, template, confidence, hits,
               origin, active, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                _rid("neg"), f"neg:{entity_name}.{predicate}", re.escape(value), predicate,
                json.dumps(template, ensure_ascii=False), 0.99, 1,
                "correction", 1, now_iso(),
            ),
        )
        self.store.conn.commit()
        return True

    def _save_rule(self, name: str, pattern: str, predicate: str, template: dict,
                   confidence: float, origin: str) -> str:
        rid = _rid("lr")
        self.store.conn.execute(
            """INSERT INTO learned_rule (id, name, pattern, predicate, template, confidence, hits,
               origin, active, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (rid, name, pattern, predicate, json.dumps(template, ensure_ascii=False),
             confidence, 0, origin, 1, now_iso()),
        )
        self.store.conn.commit()
        return rid

    def add_pattern_rule(self, pattern: str, predicate: str, operator: str = "panel",
                         confidence: float = 0.8) -> str:
        """面板里手写规则。编译不过直接抛，不静默吞。"""
        re.compile(pattern)
        rid = self._save_rule(
            name=f"manual:{predicate}", pattern=pattern, predicate=predicate,
            template={"kind": "manual"}, confidence=confidence, origin=f"operator:{operator}",
        )
        self.store.log_correction(
            assertion_id="", action="add_rule", predicate=predicate,
            new_value=pattern, operator=operator, applied_rule=rid,
        )
        return rid

    def list_rules(self) -> list[dict]:
        rows = self.store.conn.execute(
            "SELECT * FROM learned_rule ORDER BY active DESC, created_at DESC"
        )
        return [
            {
                "id": r["id"], "name": r["name"], "pattern": r["pattern"],
                "predicate": r["predicate"], "confidence": r["confidence"],
                "hits": r["hits"], "active": bool(r["active"]), "origin": r["origin"],
                "template": json.loads(r["template"] or "{}"),
            }
            for r in rows
        ]

    def toggle_rule(self, rule_id: str, active: bool) -> None:
        self.store.conn.execute(
            "UPDATE learned_rule SET active = ? WHERE id = ?", (1 if active else 0, rule_id)
        )
        self.store.conn.commit()


def negative_constraints(store: Store) -> tuple[set[tuple[str, str, str, str]], set[tuple[str, str, str]]]:
    """scoped: 绑定原句的负例；legacy: 老数据无 quote，按值拦。"""
    scoped: set[tuple[str, str, str, str]] = set()
    legacy: set[tuple[str, str, str]] = set()
    for row in store.conn.execute(
        "SELECT predicate, template FROM learned_rule WHERE active = 1 AND template LIKE '%negative%'"
    ):
        try:
            t = json.loads(row["template"])
        except json.JSONDecodeError:
            continue
        if t.get("kind") != "negative":
            continue
        eid, value = t.get("entity_id", ""), t.get("value", "")
        quote = _norm_quote(t.get("quote", ""))
        if quote:
            scoped.add((eid, row["predicate"], value, quote))
        else:
            legacy.add((eid, row["predicate"], value))
    return scoped, legacy
