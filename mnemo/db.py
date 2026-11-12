"""SQLite 持久层。

选 SQLite 而不是外部向量库的理由：交接场景的数据量在 10^4 条断言量级，
一次检索走 bigram 倒排 + 内存重排比维护一套向量库更省事，且整库单文件，
「把记忆带走」这个交接刚需天然满足。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import DEFAULT_DB

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS entity (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'thing',
    aliases     TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_entity_name ON entity(name);

CREATE TABLE IF NOT EXISTS message (
    id       TEXT PRIMARY KEY,
    channel  TEXT NOT NULL,
    speaker  TEXT NOT NULL DEFAULT '',
    ts       TEXT NOT NULL,
    text     TEXT NOT NULL,
    thread   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS assertion (
    id                TEXT PRIMARY KEY,
    entity_id         TEXT NOT NULL,
    entity_name       TEXT NOT NULL,
    predicate         TEXT NOT NULL,
    value             TEXT NOT NULL,
    slot              TEXT NOT NULL,
    source_message_id TEXT NOT NULL DEFAULT '',
    source_kind       TEXT NOT NULL DEFAULT 'im',
    source_quote      TEXT NOT NULL DEFAULT '',
    confidence        REAL NOT NULL DEFAULT 0.5,
    status            TEXT NOT NULL DEFAULT 'active',
    valid_from        TEXT,
    valid_to          TEXT,
    supersedes        TEXT,
    chain_root        TEXT,
    extractor         TEXT NOT NULL DEFAULT 'rule:unknown',
    pinned            INTEGER NOT NULL DEFAULT 0,
    is_change         INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    note              TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_assertion_slot ON assertion(slot, valid_from);
CREATE INDEX IF NOT EXISTS ix_assertion_entity ON assertion(entity_id);
CREATE INDEX IF NOT EXISTS ix_assertion_status ON assertion(status);
CREATE INDEX IF NOT EXISTS ix_assertion_source ON assertion(source_message_id);

-- bigram 倒排：中文没有空格，用二元组做索引。
-- 没用 FTS5(trigram)：它要求查询至少 3 个字符，短实体名（「张伟」「对账」）和
-- 单字谓词会漏；而且各字段权重不同（实体 1.5 / 谓词 1.2 / 取值 1.0），自己建表更好控。
CREATE TABLE IF NOT EXISTS term_index (
    term         TEXT NOT NULL,
    assertion_id TEXT NOT NULL,
    weight       REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (term, assertion_id)
);
CREATE INDEX IF NOT EXISTS ix_term ON term_index(term);

CREATE TABLE IF NOT EXISTS correction (
    id           TEXT PRIMARY KEY,
    assertion_id TEXT NOT NULL,
    action       TEXT NOT NULL,
    entity_id    TEXT NOT NULL DEFAULT '',
    predicate    TEXT NOT NULL DEFAULT '',
    old_value    TEXT NOT NULL DEFAULT '',
    new_value    TEXT NOT NULL DEFAULT '',
    operator     TEXT NOT NULL DEFAULT 'anonymous',
    ts           TEXT NOT NULL,
    applied_rule TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS learned_rule (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    pattern    TEXT NOT NULL,
    predicate  TEXT NOT NULL,
    template   TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0.6,
    hits       INTEGER NOT NULL DEFAULT 0,
    origin     TEXT NOT NULL DEFAULT '',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
    id      TEXT PRIMARY KEY,
    ts      TEXT NOT NULL,
    query   TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rejected (
    id           TEXT PRIMARY KEY,
    text         TEXT NOT NULL,
    reason       TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 0.0,
    extractor    TEXT NOT NULL DEFAULT '',
    ts           TEXT NOT NULL
);
"""


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    db_path = Path(db_path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def open_db(db_path: Path | str = DEFAULT_DB, fresh: bool = False) -> sqlite3.Connection:
    if fresh and str(db_path) != ":memory:" and Path(db_path).exists():
        Path(db_path).unlink()
        for suffix in ("-wal", "-shm"):
            side = Path(str(db_path) + suffix)
            if side.exists():
                side.unlink()
    return init_db(connect(db_path))
