"""分层抽取：分句 -> 规则 -> 未命中走模型兜底 -> 实体归一 -> 落库。"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field

from ..config import SOURCE_WEIGHTS, Settings
from ..lexicons import infer_entity_type
from ..models import Assertion, Message, now_iso
from ..store import Store
from ..feedback import negative_constraints
from .llm import BaseExtractor, build_extractor
from .rules import (
    Rule, RuleHit, clean_entity, compile_learned_rule, is_plausible_entity, match_rules,
)
from .segment import split_sentences


@dataclass
class ExtractResult:
    message_id: str
    assertions: list[Assertion] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    by_layer: dict[str, int] = field(default_factory=lambda: {"rule": 0, "llm": 0, "rejected": 0})
    entities: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "assertions": [a.id for a in self.assertions],
            "actions": self.actions,
            "by_layer": self.by_layer,
            "rejected": self.rejected,
            "entities": self.entities,
        }


def load_learned_rules(store: Store) -> list[Rule]:
    rules: list[Rule] = []
    for row in store.conn.execute(
        "SELECT * FROM learned_rule WHERE active = 1 AND template NOT LIKE '%\"negative\"%' "
        "ORDER BY confidence DESC"
    ):
        try:
            template = json.loads(row["template"] or "{}")
        except json.JSONDecodeError:
            template = {}
        rule = compile_learned_rule(
            row["pattern"], row["predicate"], row["confidence"], row["id"], row["origin"], template
        )
        if rule:
            rules.append(rule)
    return rules


def _source_factor(kind: str) -> float:
    return 0.70 + 0.30 * SOURCE_WEIGHTS.get(kind, 0.5)


class ExtractorPipeline:
    def __init__(self, store: Store, settings: Settings | None = None, extractor: BaseExtractor | None = None):
        self.store = store
        self.settings = settings or store.settings
        self.fallback: BaseExtractor = extractor or build_extractor(self.settings)
        self._rules_cache: list[Rule] | None = None
        self._negatives: tuple[set, set] | None = None

    def refresh_rules(self) -> None:
        self._rules_cache = load_learned_rules(self.store)
        self._negatives = negative_constraints(self.store)

    @property
    def learned_rules(self) -> list[Rule]:
        if self._rules_cache is None:
            self.refresh_rules()
        return self._rules_cache or []

    @property
    def negatives(self) -> tuple[set, set]:
        if self._negatives is None:
            self.refresh_rules()
        return self._negatives or (set(), set())

    def _blocked(self, entity_id: str, predicate: str, value: str, quote: str) -> bool:
        scoped, legacy = self.negatives
        norm = re.sub(r"\s+", "", quote or "")
        return (
            (entity_id, predicate, value, norm) in scoped
            or (entity_id, predicate, value) in legacy
        )

    # ------------------------------------------------------------------ 主入口
    def ingest(self, msg: Message, commit: bool = True) -> ExtractResult:
        self.store.add_message(msg)
        result = ExtractResult(message_id=msg.id)
        last_entity: str | None = None
        sentences = split_sentences(msg.text)

        for sent in sentences:
            hits = match_rules(sent, context_entity=last_entity, extra_rules=self.learned_rules)
            layer = "rule" if hits else "llm"

            if not hits:
                model_hits = self.fallback.extract(sent, context_entity=last_entity)
                hits = []
                for m in model_hits:
                    # 模型通道的实体同样要清洗，否则同一实体会裂成多条
                    ent_name = clean_entity(m.entity)
                    if not is_plausible_entity(ent_name):
                        continue
                    hits.append(
                        RuleHit(
                            entity=ent_name, predicate=m.predicate, value=m.value,
                            confidence=m.confidence, rule_id=m.extractor, quote=sent[:60],
                            entity_from_context=bool(last_entity and ent_name == last_entity),
                        )
                    )
                if not hits:
                    continue

            for hit in hits:
                if hit.entity:
                    last_entity = hit.entity
                self._persist(msg, hit, layer, result, commit)

        result.by_layer["rejected"] = len(result.rejected)
        return result

    def ingest_batch(self, messages: list[Message]) -> list[ExtractResult]:
        return [self.ingest(m) for m in messages]

    # ------------------------------------------------------------------ 落库
    def _persist(self, msg: Message, hit: RuleHit, layer: str, result: ExtractResult, commit: bool) -> None:
        conf = hit.confidence * _source_factor(msg.channel)
        if layer == "llm":
            conf = min(conf, 0.55)

        ent, quality = self._resolve_entity(hit.entity)
        conf = round(min(0.99, max(0.05, conf + quality)), 3)

        # 负例约束：只拦同一句话里的同一个错值
        if self._blocked(ent.id, hit.predicate, hit.value, hit.quote):
            self.store.log_rejected(hit.quote, "negative-constraint", conf, hit.rule_id)
            result.rejected.append({"quote": hit.quote, "conf": conf, "rule": "negative-constraint"})
            result.by_layer["rejected"] += 1
            return

        if conf < self.settings.min_extract_confidence:
            self.store.log_rejected(hit.quote, f"low-confidence:{layer}", conf, hit.rule_id)
            result.rejected.append({"quote": hit.quote, "conf": conf, "rule": hit.rule_id})
            result.by_layer["rejected"] += 1
            return

        a = Assertion(
            id=f"a_{uuid.uuid4().hex[:12]}",
            entity_id=ent.id,
            entity_name=ent.name,
            predicate=hit.predicate,
            value=hit.value,
            slot=f"{ent.id}::{hit.predicate}",
            source_message_id=msg.id,
            source_kind=msg.channel,
            source_quote=hit.quote,
            confidence=conf,
            valid_from=msg.ts if layer == "rule" else msg.ts,
            extractor=f"{layer}:{hit.rule_id}",
            is_change=hit.is_change,
        )

        if not commit:
            result.assertions.append(a)
            result.by_layer[layer] += 1
            return

        saved, action = self.store.write_assertion(a)
        result.assertions.append(saved)
        result.actions.append(f"{hit.predicate}={hit.value}:{action}")
        result.by_layer[layer] += 1
        if ent.name not in result.entities:
            result.entities.append(ent.name)

    def _resolve_entity(self, name: str) -> tuple:
        """实体归一，返回 (实体, 置信度调整量)；精确 > 别名 > 模糊 > 新建。"""
        name = name.strip()
        exact = self.store.conn.execute("SELECT * FROM entity WHERE name = ?", (name,)).fetchone()
        if exact:
            return self.store.get_entity(exact["id"]), 0.03

        for row in self.store.conn.execute("SELECT * FROM entity"):
            aliases = json.loads(row["aliases"] or "[]")
            if name in aliases:
                return self.store.get_entity(row["id"]), 0.02

        # 模糊：同一类型且差异极小（如 支付网关 vs 支付网关系统）
        best, best_ratio = None, 0.0
        import difflib

        for row in self.store.conn.execute("SELECT * FROM entity"):
            ratio = difflib.SequenceMatcher(None, name, row["name"]).ratio()
            if ratio > best_ratio:
                best, best_ratio = row, ratio
        if best is not None and best_ratio >= 0.88 and infer_entity_type(name) == best["type"]:
            ent = self.store.get_entity(best["id"])
            if ent:
                merged = sorted(set(ent.aliases) | {name})
                self.store.conn.execute(
                    "UPDATE entity SET aliases = ? WHERE id = ?",
                    (json.dumps(merged, ensure_ascii=False), ent.id),
                )
                self.store.conn.commit()
                return ent, -0.04

        ent = self.store.upsert_entity(name)
        return ent, -0.02
