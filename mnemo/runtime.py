"""运行期事件总线：节点只往 Bus 发事件，由外层决定输出到终端还是 SSE。

Bus 用 run_id 注册表传递，不用 contextvars —— LangGraph 在工作线程里跑节点，
contextvar 不会自动传播。
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

