"""Agent 工具层：把检索、版本链、实体表、冲突、溯源包成模型可调用的函数。"""
from __future__ import annotations

from typing import Any

from ..models import Assertion
from ..retrieve import parse_query, predicate_label, retrieve
from ..store import Store


class ToolError(Exception):
    """参数不合法或对象不存在。错误文本会原样回给模型，让它自己纠正。"""


def _brief(a: Assertion, score: float | None = None) -> dict:
    d = {
        "id": a.id,
        "entity": a.entity_name,
        "predicate": a.predicate,
        "label": predicate_label(a.predicate),
        "value": a.value,
        "status": a.status,
        "confidence": round(a.confidence, 2),
        "valid_from": (a.valid_from or "")[:10],
        "quote": a.source_quote,
    }
    if score is not None:
        d["score"] = round(score, 2)
    return d


class Toolbox:
    """一个 Store 对应一个工具箱；每次 ask 复用一个实例。"""

    def __init__(self, store: Store):
        self.store = store
        self.calls: list[dict] = []

    # ------------------------------------------------------------------ 工具
    def search_memory(self, query: str, limit: int = 6, status: str = "active") -> dict:
        if not str(query or "").strip():
            raise ToolError("query 不能为空，写一句自然语言问题")
        limit = max(1, min(int(limit or 6), 12))
        q = parse_query(self.store, str(query))
        cands = retrieve(self.store, q, limit=limit * 4)
        if status != "all":
            cands = [c for c in cands if c.assertion.status == status]
        rows = [_brief(c.assertion, c.score) for c in cands[:limit]]
        return {
            "query": query,
            "parsed": {"entities": q.entities, "intents": q.intents, "terms": q.terms[:8]},
            "count": len(rows),
            "results": rows,
            "hint": "" if rows else "没有命中。可以换同义词、只用实体名、或先用 list_entities 确认叫法。",
        }

    def read_slot(self, entity: str, predicate: str) -> dict:
        ent = self._entity(entity)
        slot = f"{ent.id}::{predicate}"
        chain = self.store.chain(slot)
        if not chain:
            raise ToolError(f"{ent.name} 没有 {predicate} 这个字段，可用 list_entities 看它有哪些字段")
        return {
            "entity": ent.name,
            "predicate": predicate,
            "label": predicate_label(predicate),
            "current": _brief(chain[-1]),
            "chain": [_brief(a) for a in chain],
        }

    def list_entities(self, keyword: str = "") -> dict:
        counts: dict[str, int] = {}
        preds: dict[str, set[str]] = {}
        for a in self.store.all_assertions():
            if a.status not in ("active", "disputed"):
                continue
            counts[a.entity_id] = counts.get(a.entity_id, 0) + 1
            preds.setdefault(a.entity_id, set()).add(a.predicate)
        rows = []
        for e in self.store.all_entities():
            if keyword and keyword not in e.name and not any(keyword in x for x in e.aliases):
                continue
            rows.append({
                "name": e.name,
                "type": e.type,
                "aliases": e.aliases,
                "predicates": sorted(preds.get(e.id, ())),
                "assertions": counts.get(e.id, 0),
            })
        rows.sort(key=lambda r: -r["assertions"])
        return {"keyword": keyword, "count": len(rows), "entities": rows}

    def check_conflicts(self) -> dict:
        active: dict[str, list[Assertion]] = {}
        disputed: dict[str, list[Assertion]] = {}
        for a in self.store.all_assertions():
            if a.status == "active":
                active.setdefault(a.slot, []).append(a)
            elif a.status == "disputed":
                disputed.setdefault(a.slot, []).append(a)
        rows = []
        for slot in set(active) | set(disputed):
            items = active.get(slot, []) + disputed.get(slot, [])
            if len(items) < 2:
                continue
            head = items[0]
            rows.append({
                "entity": head.entity_name,
                "predicate": head.predicate,
                "label": predicate_label(head.predicate),
                "values": [_brief(a) for a in items],
            })
        return {"count": len(rows), "conflicts": rows}

    def trace_source(self, assertion_id: str) -> dict:
        a = self.store.get_assertion(assertion_id)
        if not a:
            raise ToolError(f"没有 id 为 {assertion_id} 的断言")
        msg = self.store.get_message(a.source_message_id)
        return {
            "assertion": _brief(a),
            "message": None if not msg else {
                "id": msg.id, "channel": msg.channel, "speaker": msg.speaker,
                "ts": msg.ts, "text": msg.text,
            },
        }

    # ------------------------------------------------------------------ 分发
    def _entity(self, name: str):
        name = str(name or "").strip()
        if not name:
            raise ToolError("entity 不能为空")
        ents = self.store.all_entities()
        for e in ents:
            if e.name == name:
                return e
        for e in ents:
            if name in e.aliases or any(name in x or x in name for x in e.aliases):
                return e
        hits = [e for e in ents if name in e.name or e.name in name]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise ToolError(f"「{name}」匹配到多个实体：{[e.name for e in hits]}，请写全名")
        raise ToolError(f"没有「{name}」这个实体，用 list_entities 查可用的名字")

    def call(self, name: str, args: dict[str, Any]) -> dict:
        fn = getattr(self, str(name or ""), None)
        if name not in SCHEMA_NAMES or not callable(fn):
            raise ToolError(f"没有工具 {name}，可用工具：{sorted(SCHEMA_NAMES)}")
        payload = dict(args or {})
        try:
            out = fn(**payload)
        except TypeError as exc:
            raise ToolError(f"{name} 参数不对：{exc}；schema 见工具定义") from exc
        self.calls.append({"tool": name, "args": payload})
        return out

    def schemas(self) -> list[dict]:
        return SCHEMAS


SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "按自然语言检索记忆库，返回候选断言（含 id、实体、谓词、取值、置信度、原文片段）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索语句，可以是问句"},
                    "limit": {"type": "integer", "description": "返回条数，默认 6，最大 12"},
                    "status": {"type": "string", "enum": ["active", "superseded", "disputed", "all"],
                               "description": "只看某种状态，默认 active"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_slot",
            "description": "读某个实体某个字段的完整版本链（含已被取代的历史值），用于回答「变过没有」。",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "实体名"},
                    "predicate": {"type": "string", "description": "字段名，如 owner / deadline / status"},
                },
                "required": ["entity", "predicate"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_entities",
            "description": "列出实体及其已有字段，用于确认叫法、发现同名或别名。",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "可选，按名字过滤"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_conflicts",
            "description": "列出所有存在多值或待确认的字段。回答前应当查一次，冲突必须并列呈现。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_source",
            "description": "给定断言 id，返回它的原始消息（谁、在哪、什么时候说的、原话）。",
            "parameters": {
                "type": "object",
                "properties": {"assertion_id": {"type": "string"}},
                "required": ["assertion_id"],
            },
        },
    },
]

SCHEMA_NAMES = {s["function"]["name"] for s in SCHEMAS}
