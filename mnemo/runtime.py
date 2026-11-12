"""运行期事件总线。

节点内部不直接依赖 SSE / 日志，只往总线上发事件；由外层决定是打到终端、
推到 SSE 连接，还是存进 run_log。这样「可观测」是编排层的事，业务节点不必知道。

注意：不用 contextvars 传 bus。LangGraph 会把节点丢进工作线程执行，
contextvar 不会自动传播（实测只有第一个节点能拿到），所以改成一张
run_id -> Bus 的注册表，节点通过 state 里的 run_id 取。多线程安全。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .models import TraceEvent


@dataclass
class Bus:
    events: list[TraceEvent] = field(default_factory=list)
    sink: Callable[[TraceEvent], None] | None = None

    def emit(self, stage: str, **payload: Any) -> TraceEvent:
        ev = TraceEvent(stage=stage, payload=payload)
        self.events.append(ev)
        if self.sink:
            try:
                self.sink(ev)
            except Exception:
                pass
        return ev

    def drain(self) -> list[TraceEvent]:
        out, self.events = self.events, []
        return out


_REGISTRY: dict[str, Bus] = {}
_LOCK = threading.Lock()


def register_run(run_id: str, bus: Bus) -> None:
    with _LOCK:
        _REGISTRY[run_id] = bus


def get_bus(run_id: str | None) -> Bus | None:
    if not run_id:
        return None
    with _LOCK:
        return _REGISTRY.get(run_id)


def unregister_run(run_id: str) -> None:
    with _LOCK:
        _REGISTRY.pop(run_id, None)

