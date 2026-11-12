"""token 估算。

不引 tiktoken：目标是「同一套估算口径下比较注入量与全量拼接量」的相对比例，
不需要绝对精确。口径：中文 1 字 ≈ 0.75 token，ASCII 每 4 字符 ≈ 1 token，
再对每条记忆加固定结构开销（标签/来源/时间）。三处用法共用这一份实现，
所以比值口径一致。
"""
from __future__ import annotations

import re

_CJK = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
_ASCII = re.compile(r"[\x00-\x7f]")

PER_MEMORY_OVERHEAD = 6      # “- [owner] 张伟 (0.92, 来源 im#12)” 这类标签的固定开销
PER_MESSAGE_OVERHEAD = 4     # 全量拼接时每条消息的“发言人(时间):”前缀


def estimate_tokens(text: str) -> float:
    if not text:
        return 0.0
    cjk = len(_CJK.findall(text))
    ascii_chars = len(_ASCII.findall(text))
    other = max(0, len(text) - cjk - ascii_chars)
    return cjk * 0.75 + ascii_chars / 4.0 + other * 0.75


def render_assertion_line(a, *, with_source: bool = True) -> str:
    """一条记忆渲染成注入行。评分最高的一条会带时间与来源，供模型判断新旧。"""
    head = f"- [{a.predicate}] {a.entity_name} → {a.value}"
    if not with_source:
        return head
    bits = [f"置信 {a.confidence:.2f}", f"状态 {a.status}"]
    if a.valid_from:
        bits.append(f"自 {a.valid_from[:10]}")
    if a.source_message_id:
        bits.append(f"来源 {a.source_kind}:{a.source_message_id}")
    return head + " (" + ", ".join(bits) + ")"


def memory_tokens(a) -> float:
    return estimate_tokens(render_assertion_line(a)) + PER_MEMORY_OVERHEAD


def message_tokens(m) -> float:
    return estimate_tokens(m.text) + PER_MESSAGE_OVERHEAD


def full_concat_tokens(messages) -> float:
    """基线：把所有历史消息拼进上下文（不筛选）。"""
    return sum(message_tokens(m) for m in messages)
