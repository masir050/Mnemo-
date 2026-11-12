"""Agent 主循环：模型决定调哪个工具、看结果、判断何时停。"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Settings
from ..store import Store
from .model import ChatModel, ModelError, ToolCall
from .prompt import SYSTEM_PROMPT
from .tools import Toolbox, ToolError

CITE_RE = re.compile(r"\[(a_[0-9a-z]{6,24})\]", re.I)
MAX_TOOL_CHARS = 3000


@dataclass
class Step:
    index: int
    kind: str                      # thought / tool_call / observation / answer
    content: str = ""
    tool: str = ""
    args: dict = field(default_factory=dict)
    ok: bool = True
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "index": self.index, "kind": self.kind, "content": self.content,
            "tool": self.tool, "args": self.args, "ok": self.ok,
            "latency_ms": self.latency_ms,
        }


@dataclass
class AgentResult:
    question: str
    answer: str
    steps: list[Step]
    citations: list[dict]
    stop_reason: str = "answer"
    usage: dict = field(default_factory=dict)
    elapsed_ms: int = 0
    tool_calls: int = 0
    invalid_citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "answer": self.answer,
            "steps": [s.to_dict() for s in self.steps],
            "citations": self.citations,
            "stop_reason": self.stop_reason,
            "usage": self.usage,
            "elapsed_ms": self.elapsed_ms,
            "tool_calls": self.tool_calls,
            "invalid_citations": self.invalid_citations,
        }


def _clip(text: str, n: int = MAX_TOOL_CHARS) -> str:
    return text if len(text) <= n else text[:n] + f"…（已截断，原 {len(text)} 字）"


class AgentRunner:
    def __init__(self, store: Store, model: ChatModel, settings: Settings | None = None):
        self.store = store
        self.model = model
        self.settings = settings or store.settings

    def run(self, question: str, on_event: Callable[[Step], None] | None = None) -> AgentResult:
        settings = self.settings
        box = Toolbox(self.store)
        steps: list[Step] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        t0 = time.perf_counter()

        def emit(step: Step) -> None:
            steps.append(step)
            if on_event:
                on_event(step)

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        tools = box.schemas()
        calls_used = 0
        answer, stop_reason = "", "answer"

        for i in range(settings.agent_max_steps):
            try:
                reply = self.model.chat(messages, tools=tools)
            except ModelError as exc:
                stop_reason = "model_error"
                answer = f"模型调用失败：{exc}"
                break
            for k in usage:
                usage[k] += int((reply.usage or {}).get(k) or 0)

            if reply.content.strip() and reply.tool_calls:
                emit(Step(len(steps), "thought", reply.content.strip(), latency_ms=reply.latency_ms))

            if not reply.tool_calls:
                answer = reply.content.strip()
                break

            messages.append({
                "role": "assistant",
                "content": reply.content or "",
                "tool_calls": [
                    {"id": c.id or f"call_{i}", "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.args, ensure_ascii=False)}}
                    for c in reply.tool_calls
                ],
            })
            for call in reply.tool_calls:
                if calls_used >= settings.agent_max_tool_calls:
                    stop_reason = "tool_budget"
                    break
                calls_used += 1
                result, ok = self._execute(box, call)
                emit(Step(len(steps), "tool_call", f"{call.name}", call.name, call.args, ok))
                emit(Step(len(steps), "observation", _clip(json.dumps(result, ensure_ascii=False)),
                          call.name, ok=ok))
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id or f"call_{i}",
                    "content": _clip(json.dumps(result, ensure_ascii=False)),
                })
            if stop_reason == "tool_budget":
                break
        else:
            stop_reason = "max_steps"

        if not answer and stop_reason in ("max_steps", "tool_budget"):
            answer = self._wrap_up(messages, stop_reason, usage)

        citations, invalid = self._citations(answer)
        elapsed = int((time.perf_counter() - t0) * 1000)
        result = AgentResult(
            question=question, answer=answer, steps=steps, citations=citations,
            stop_reason=stop_reason, usage=usage, elapsed_ms=elapsed,
            tool_calls=calls_used, invalid_citations=invalid,
        )
        try:
            self.store.log_run(question, {"kind": "agent", **result.to_dict()})
        except Exception:
            pass
        return result

    # ------------------------------------------------------------------ 内部
    def _execute(self, box: Toolbox, call: ToolCall) -> tuple[dict, bool]:
        try:
            return box.call(call.name, call.args), True
        except ToolError as exc:
            return {"error": str(exc)}, False

    def _wrap_up(self, messages: list[dict], reason: str, usage: dict) -> str:
        """预算耗尽时不再调工具，只让模型基于已有观察收口。"""
        hint = "已经用完检索步数" if reason == "max_steps" else "已经用完工具调用次数"
        try:
            reply = self.model.chat(
                messages + [{"role": "user",
                             "content": f"{hint}，不要再调工具，直接基于上面的观察给结论；依据不足就明说缺什么。"}],
                tools=None,
            )
            for k in usage:
                usage[k] += int((reply.usage or {}).get(k) or 0)
            return reply.content.strip() or "（未能在限定步数内得出结论）"
        except ModelError as exc:
            return f"未能在限定步数内得出结论：{exc}"

    def _citations(self, answer: str) -> tuple[list[dict], list[str]]:
        seen: list[str] = []
        for m in CITE_RE.finditer(answer or ""):
            if m.group(1) not in seen:
                seen.append(m.group(1))
        out, invalid = [], []
        for aid in seen:
            a = self.store.get_assertion(aid)
            if not a:
                invalid.append(aid)
                continue
            msg = self.store.get_message(a.source_message_id)
            out.append({
                "assertion_id": a.id,
                "entity": a.entity_name,
                "predicate": a.predicate,
                "value": a.value,
                "source_quote": a.source_quote,
                "source_kind": a.source_kind,
                "source_message_id": a.source_message_id,
                "speaker": msg.speaker if msg else "",
                "ts": (msg.ts if msg else a.valid_from or "")[:10],
            })
        return out, invalid
