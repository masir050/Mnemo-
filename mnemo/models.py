"""领域模型。

核心是「实体 — 谓词 — 断言」三元结构：断言不是一个裸的键值对，
而是带来源、时间窗、置信度与状态机的一条可追溯记录。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Literal

Status = Literal["active", "superseded", "expired", "disputed"]

# 状态机的合法迁移。任何越界迁移都会被 store 拒绝并记一条告警。
TRANSITIONS: dict[str, set[str]] = {
    "active": {"superseded", "expired", "disputed"},
    "disputed": {"active", "superseded", "expired"},
    "superseded": {"active"},      # 只在人工纠正「撤销误判」时回退
    "expired": {"active"},         # 人工续期
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class Entity:
    id: str
    name: str
    type: str = "thing"          # person / system / project / process / org / thing
    aliases: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]


@dataclass
class Message:
    """原始消息。断言必须能回溯到这里的某一条，这是可追溯性的底线。"""
    id: str
    channel: str                 # im / ticket / weekly / doc
    speaker: str
    ts: str
    text: str
    thread: str = ""


@dataclass
class Assertion:
    id: str
    entity_id: str
    entity_name: str
    predicate: str
    value: str
    slot: str                    # entity_id::predicate，版本链挂在 slot 上
    source_message_id: str
    source_kind: str             # im / ticket / weekly / manual / llm
    source_quote: str            # 原文片段，用于面板回溯
    confidence: float
    status: Status = "active"
    valid_from: str | None = None
    valid_to: str | None = None
    supersedes: str | None = None
    chain_root: str | None = None
    extractor: str = "rule:unknown"
    pinned: bool = False
    is_change: bool = False      # 原文含「改成/转给/延期到」这类显式变更标记
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    note: str = ""

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["pinned"] = 1 if self.pinned else 0
        row["is_change"] = 1 if self.is_change else 0
        return row

    @classmethod
    def from_row(cls, row: Any) -> "Assertion":
        data = dict(row)
        data["pinned"] = bool(data.get("pinned", 0))
        data["is_change"] = bool(data.get("is_change", 0))
        data.pop("rowid", None)
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        clean = {k: v for k, v in data.items() if k in allowed}
        return cls(**clean)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Candidate:
    """检索阶段的一条候选，带三维打分明细（可观测性：分数要能解释）。"""
    assertion: Assertion
    relevance: float = 0.0
    recency: float = 0.0
    importance: float = 0.0
    score: float = 0.0
    hop: int = 0
    matched_terms: list[str] = field(default_factory=list)

    @property
    def breakdown(self) -> dict[str, float]:
        return {
            "relevance": round(self.relevance, 4),
            "recency": round(self.recency, 4),
            "importance": round(self.importance, 4),
            "score": round(self.score, 4),
        }


@dataclass
class TraceEvent:
    """一个可观测事件，SSE 直接吐它。"""
    stage: str
    ts: str = field(default_factory=now_iso)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"stage": self.stage, "ts": self.ts, **self.payload},
            ensure_ascii=False,
        )
