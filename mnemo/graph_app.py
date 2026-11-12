"""LangGraph 编排：解析 -> 检索 ->（召回不足则放宽）-> 消解 -> 组装 -> 成文。"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, TypedDict

from .assemble import ContextBundle, assemble
from .config import Settings
from .conflict import ResolvedFact, resolve_candidates
from .models import Assertion, TraceEvent
from .retrieve import parse_query, predicate_label, retrieve
from .runtime import Bus, get_bus, register_run, unregister_run
from .store import Store
from .tokens import estimate_tokens, memory_tokens

try:  # 没有 langgraph 时走内置兼容层
    from langgraph.graph import END, START, StateGraph  # type: ignore

    GRAPH_BACKEND = "langgraph"
except Exception:  # pragma: no cover - 环境差异
    from .graph_compat import END, START, StateGraph  # type: ignore

    GRAPH_BACKEND = "compat"


CHANNEL_CN = {
    "im": "IM 群聊", "ticket": "工单", "weekly": "周报", "doc": "文档",
    "manual": "人工确认", "llm": "模型抽取",
}


def _cite_label(store: Store, kind: str, message_id: str) -> str:
    """来源展示成「IM 群聊 · 张伟 · 10/08」。"""
    msg = store.get_message(message_id) if message_id else None
    if msg is None:
        return CHANNEL_CN.get(kind, kind)
    bits = [CHANNEL_CN.get(msg.channel or kind, kind)]
    if msg.speaker:
        bits.append(msg.speaker)
    if msg.ts:
        bits.append(msg.ts[5:10].replace("-", "/"))
    return " · ".join(bits)


class MemState(TypedDict, total=False):
    run_id: str
    query: str
    parsed: dict
    widen: int
    candidates: list
    facts: list
    bundle: dict
    context_text: str
    answer: str
    citations: list


def _emit(state: MemState, stage: str, **payload) -> None:
    """节点内的唯一上报口；取不到 bus 就丢弃。"""
    bus = get_bus(state.get("run_id"))
    if bus is not None:
        bus.emit(stage, **payload)


# --------------------------------------------------------------------------- 节点
def _node_parse(store: Store):
    def run(state: MemState) -> dict:
        q = parse_query(store, state["query"])
        _emit(state, "parse", query=q.text, entities=q.entities, intents=q.intents, terms=len(q.terms))
        return {"parsed": q.as_dict(), "widen": state.get("widen", 0), "_qobj": q}
    return run


def _node_retrieve(store: Store, settings: Settings):
    def run(state: MemState) -> dict:
        q = state.get("_qobj") or parse_query(store, state["query"])
        widen = state.get("widen", 0)
        local = settings.with_overrides(
            min_score=max(0.04, settings.min_score * (0.6 ** widen)),
            max_candidates=settings.max_candidates * (2 ** widen),
        ) if widen else settings
        cands = retrieve(store, q, settings=local)
        _emit(
            state, "retrieve",
            widen=widen,
            candidates=len(cands),
            top=[
                {"id": c.assertion.id, "entity": c.assertion.entity_name,
                 "predicate": c.assertion.predicate, "value": c.assertion.value,
                 **c.breakdown}
                for c in cands[:6]
            ],
        )
        return {"candidates": cands, "parsed": q.as_dict(), "_qobj": q}
    return run


def _route_after_retrieve(state: MemState) -> str:
    if len(state.get("candidates") or []) < 4 and state.get("widen", 0) < 1:
        return "widen"
    return "ok"


def _node_widen(store: Store) -> Any:
    def run(state: MemState) -> dict:
        _emit(state, "widen", reason="召回不足，放宽阈值重检", from_widen=state.get("widen", 0))
        return {"widen": state.get("widen", 0) + 1}
    return run


def _node_resolve(store: Store):
    def run(state: MemState) -> dict:
        facts = resolve_candidates(store, state.get("candidates") or [])
        _emit(
            state, "resolve",
            slots=len(facts),
            conflicts=[f.to_dict() for f in facts if f.conflict],
        )
        return {"facts": facts}
    return run


def _node_assemble(store: Store, settings: Settings):
    def run(state: MemState) -> dict:
        facts: list[ResolvedFact] = state.get("facts") or []
        bundle: ContextBundle = assemble(state.get("candidates") or [], facts, settings.token_budget)
        _emit(
            state, "assemble",
            tokens_used=round(bundle.tokens_used, 1),
            budget=bundle.budget,
            utilization=bundle.utilization,
            included=len(bundle.included),
            dropped=len(bundle.dropped),
            collapsed=bundle.redundant_collapsed,
        )
        return {"bundle": bundle.as_dict(), "context_text": bundle.text(), "_bundle": bundle}
    return run


def _node_compose(store: Store):
    def run(state: MemState) -> dict:
        facts: list[ResolvedFact] = state.get("facts") or []
        parsed = state.get("parsed") or {}
        answer, citations = compose_answer(state["query"], facts, parsed.get("entities"))
        for c in citations:
            c["source_label"] = _cite_label(store, c["source_kind"], c["source_message_id"])
        _emit(state, "compose", answer=answer, citations=len(citations))
        return {"answer": answer, "citations": citations}
    return run


# --------------------------------------------------------------------------- 成文
def compose_answer(query: str, facts: list[ResolvedFact], entities: list[str] | None = None) -> tuple[str, list[dict]]:
    """把消解后的结论组织成「现值 + 冲突 + 轨迹」；置信度 <0.5 的不进正文。"""
    live = [f for f in facts if f.current]
    if not live:
        return "没有检索到相关记忆。", []

    # 点名的实体排在前面
    if entities:
        live.sort(key=lambda f: (f.entity_name not in entities, -f.best_score))

    solid = [f for f in live if f.confidence >= 0.5]
    weak = [f for f in live if f.confidence < 0.5]
    if not solid:
        solid, weak = live[:3], live[3:]

    lines: list[str] = []
    citations: list[dict] = []
    for fact in solid[:6]:
        cur = fact.current
        assert cur is not None
        head = f"{fact.entity_name}｜{predicate_label(fact.predicate)}：{cur.value}"
        if fact.conflict and fact.alternates:
            alts = "、".join(f"{a.value}（置信 {a.confidence:.2f}）" for a in fact.alternates)
            head += f" —— 注意：另有不同记录 {alts}，需确认"
        lines.append(head)
        if fact.history:
            seq = " → ".join(
                f"{a.value}@{a.valid_from[:10] if a.valid_from else '?'}" for a in fact.history
            )
            lines.append(f"    变更轨迹：{seq} → {cur.value}")
        citations.append(
            {
                "assertion_id": cur.id,
                "entity": fact.entity_name,
                "predicate": fact.predicate,
                "value": cur.value,
                "confidence": round(fact.confidence, 2),
                "source": f"{cur.source_kind}:{cur.source_message_id}",
                "source_kind": cur.source_kind,
                "source_message_id": cur.source_message_id,
                "source_quote": cur.source_quote,
            }
        )

    if weak:
        lines.append(f"（另有 {len(weak)} 条低置信记录待确认）")
    if len(solid) > 6:
        lines.append(f"（另有 {len(solid) - 6} 条相关记忆未展开）")
    return "\n".join(lines), citations


# --------------------------------------------------------------------------- 编排
@dataclass
class RunResult:
    query: str
    answer: str
    facts: list[dict] = field(default_factory=list)
    bundle: dict = field(default_factory=dict)
    citations: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    backend: str = GRAPH_BACKEND

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "answer": self.answer,
            "facts": self.facts,
            "bundle": self.bundle,
            "citations": self.citations,
            "trace": self.trace,
            "backend": self.backend,
        }


def _wire(g, store: Store, settings: Settings):
    """把节点和边接到 StateGraph 实现上。"""
    g.add_node("parse", _node_parse(store))
    g.add_node("retrieve", _node_retrieve(store, settings))
    g.add_node("widen", _node_widen(store))
    g.add_node("resolve", _node_resolve(store))
    g.add_node("assemble", _node_assemble(store, settings))
    g.add_node("compose", _node_compose(store))

    g.add_edge(START, "parse")
    g.add_edge("parse", "retrieve")
    g.add_conditional_edges("retrieve", _route_after_retrieve, {"widen": "widen", "ok": "resolve"})
    g.add_edge("widen", "retrieve")
    g.add_edge("resolve", "assemble")
    g.add_edge("assemble", "compose")
    g.add_edge("compose", END)
    return g


class MnemoRunner:
    def __init__(self, store: Store, settings: Settings | None = None):
        self.store = store
        self.settings = settings or store.settings
        self.graph = _wire(StateGraph(MemState), store, self.settings).compile()

    # ------------------------------------------------------------------ 同步
    def run(self, query: str) -> RunResult:
        run_id = uuid.uuid4().hex
        bus = Bus()
        register_run(run_id, bus)
        try:
            final = self.graph.invoke({"run_id": run_id, "query": query, "widen": 0})
        finally:
            unregister_run(run_id)
        result = RunResult(
            query=query,
            answer=final.get("answer", ""),
            facts=[f.to_dict() for f in final.get("facts") or []],
            bundle=final.get("bundle") or {},
            citations=final.get("citations") or [],
            trace=[json.loads(e.to_json()) for e in bus.events],
        )
        self.store.log_run(query, {
            "answer": result.answer,
            "tokens": result.bundle.get("tokens_used"),
            "citations": [c["assertion_id"] for c in result.citations],
        })
        return result

    # ------------------------------------------------------------------ 流式
    def stream(self, query: str) -> Iterator[TraceEvent]:
        """按节点粒度回传事件。"""
        run_id = uuid.uuid4().hex
        bus = Bus()
        register_run(run_id, bus)
        state: dict = {"run_id": run_id, "query": query, "widen": 0}
        try:
            for snapshot in self.graph.stream(
                {"run_id": run_id, "query": query, "widen": 0}, stream_mode="values"
            ):
                state = snapshot
                for ev in bus.drain():
                    yield ev
            for ev in bus.drain():
                yield ev
            yield TraceEvent(stage="final", payload={
                "answer": state.get("answer", ""),
                "facts": [f.to_dict() for f in (state.get("facts") or [])],
                "bundle": state.get("bundle") or {},
                "citations": state.get("citations") or [],
            })
        finally:
            unregister_run(run_id)


def context_overhead(facts: list[ResolvedFact], bundle_tokens: float) -> dict:
    """注入量相对全量事实拼接的占比。"""
    raw = sum(memory_tokens(f.current) for f in facts if f.current)
    return {
        "assembled": round(bundle_tokens, 1),
        "full_facts": round(raw, 1),
        "ratio": round(bundle_tokens / raw, 3) if raw else 0.0,
    }
