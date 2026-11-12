"""LangGraph 缺失时的最小兼容实现：StateGraph / add_node / add_edge / compile / invoke / stream。"""
from __future__ import annotations

from typing import Any, Callable, Iterator

START = "__start__"
END = "__end__"


class _CompiledGraph:
    def __init__(self, nodes: dict[str, Callable], edges: dict[str, str],
                 cond: dict[str, tuple[Callable, dict[str, str]]], entry: str, max_steps: int):
        self.nodes = nodes
        self.edges = edges
        self.cond = cond
        self.entry = entry
        self.max_steps = max_steps

    def _next(self, node: str, state: dict) -> str | None:
        if node in self.cond:
            router, mapping = self.cond[node]
            key = router(state)
            target = mapping.get(key)
            return None if target in (END, None) else target
        target = self.edges.get(node)
        return None if target in (END, None) else target

    def stream(self, state: dict, stream_mode: str = "values", **_: Any) -> Iterator[dict]:
        cur = self.entry
        steps = 0
        while cur is not None and steps < self.max_steps:
            updates = self.nodes[cur](dict(state))
            if updates:
                if stream_mode == "updates":
                    yield {cur: updates}
                else:
                    state = {**state, **updates}
                    yield dict(state)
            cur = self._next(cur, state)
            steps += 1

    def invoke(self, state: dict, **_: Any) -> dict:
        out = dict(state)
        for s in self.stream(out, stream_mode="values"):
            out = s
        return out


class StateGraph:
    def __init__(self, state_schema=None):
        self.state_schema = state_schema
        self._nodes: dict[str, Callable] = {}
        self._edges: dict[str, str] = {}
        self._cond: dict[str, tuple[Callable, dict[str, str]]] = {}
        self._entry: str | None = None
        self._max_steps = 64

    def add_node(self, name: str, fn: Callable) -> "StateGraph":
        self._nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        if src == START:
            self._entry = dst
        else:
            self._edges[src] = dst
        return self

    def add_conditional_edges(self, src: str, router: Callable, mapping: dict[str, str]) -> "StateGraph":
        self._cond[src] = (router, mapping)
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        self._entry = name
        return self

    def compile(self, **_: Any) -> _CompiledGraph:
        if not self._entry:
            raise ValueError("graph 未设置入口节点")
        return _CompiledGraph(self._nodes, self._edges, self._cond, self._entry, self._max_steps)
