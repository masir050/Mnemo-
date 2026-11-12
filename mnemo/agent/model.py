"""Agent 的模型客户端。OpenAI 兼容协议，加一个可脚本化的假模型用于测试。"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..config import Settings


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class ChatReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0
    raw: dict = field(default_factory=dict)


class ChatModel(Protocol):
    name: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatReply: ...


class ModelError(RuntimeError):
    pass


class OpenAICompatChat:
    """任意 OpenAI 兼容端点（DeepSeek / vLLM / Ollama / 云端）。"""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.name = self.settings.llm_model

    def available(self) -> bool:
        return bool(self.settings.llm_base_url and self.settings.llm_api_key)

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatReply:
        if not self.available():
            raise ModelError("模型未配置：需要 MNEMO_LLM_BASE_URL 与 MNEMO_LLM_API_KEY")
        import httpx

        body = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": 0.0,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        t0 = time.perf_counter()
        try:
            r = httpx.post(
                f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                json=body,
                timeout=self.settings.agent_timeout,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            raise ModelError(f"模型调用失败：{type(exc).__name__} {str(exc)[:200]}") from exc
        ms = int((time.perf_counter() - t0) * 1000)
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"__raw__": fn.get("arguments")}
            calls.append(ToolCall(id=c.get("id", ""), name=fn.get("name", ""), args=args))
        return ChatReply(
            content=msg.get("content") or "",
            tool_calls=calls,
            usage=data.get("usage") or {},
            latency_ms=ms,
            raw=data,
        )


class ScriptedChat:
    """按预设脚本返回，用于离线测试循环本身。"""

    name = "scripted"

    def __init__(self, replies: list[ChatReply] | Callable[[list[dict]], ChatReply]):
        self._replies = replies
        self._i = 0
        self.seen: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatReply:
        self.seen.append([dict(m) for m in messages])
        if callable(self._replies):
            return self._replies(messages)
        if self._i >= len(self._replies):
            return ChatReply(content="脚本已用完")
        out = self._replies[self._i]
        self._i += 1
        return out


def build_model(settings: Settings) -> ChatModel:
    model = OpenAICompatChat(settings)
    if model.available():
        return model
    raise ModelError(
        "没有可用模型。请在 .env 里配置 MNEMO_LLM_BASE_URL / MNEMO_LLM_API_KEY / MNEMO_LLM_MODEL"
    )


def mask_key(key: str) -> str:
    if not key:
        return "(未配置)"
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "***"


if __name__ == "__main__":
    m = build_model(Settings())
    print("model:", m.name, "| key:", mask_key(os.environ.get("MNEMO_LLM_API_KEY", "")))
    print(m.chat([{"role": "user", "content": "回复 OK"}]).content)
