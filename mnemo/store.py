"""记忆存储：实体归一、断言写入、版本链与状态机。

写入路径上做了三件事，都是「旧值不丢失」这个前提的推论：
1. 同值反复出现 -> 合并证据（Noisy-OR 提升置信度），不产生新版本；
2. 值变了 -> 旧版本降权置 superseded，保留在链上供追溯；
3. 与人工确认值冲突 -> 新值只能进 disputed，等人裁决，不静默覆盖。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from .config import DEFAULT_SETTINGS, SOURCE_WEIGHTS, Settings
from .db import open_db
from .lexicons import infer_entity_type, importance_of
from .models import Assertion, Entity, Message, TRANSITIONS, parse_iso, now_iso


def _nid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _eid(name: str) -> str:
    return "e_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def bigrams(text: str) -> list[str]:
    """中文二元切分 + 英文/数字按词。去噪后返回。

    中文必须切成二元组，不能切成单字：单字索引会把「交付」和「支付」判成相关
    （共享一个「付」），一条无关记忆就凭这个字挤进上下文。二元组没有这个问题，
    代价是丢掉了「负责人」与「负责」之间的模糊匹配——在交接语料里可以接受。
    """
    text = (text or "").strip().lower()
    if not text:
        return []

    out: list[str] = []
    cjk: list[str] = []
    buf: list[str] = []

    def flush_cjk() -> None:
        if not cjk:
            return
        if len(cjk) == 1:
            out.append(cjk[0])
        else:
            out.extend("".join(cjk[i : i + 2]) for i in range(len(cjk) - 1))
        cjk.clear()

    def flush_word() -> None:
        if buf:
            out.append("".join(buf))
            buf.clear()

    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            flush_word()
            cjk.append(ch)
        elif ch.isalnum() or ch in "-_.":
            flush_cjk()
            buf.append(ch)
        else:
            flush_cjk()
            flush_word()
    flush_cjk()
    flush_word()
    return out


class Store:
    def __init__(self, db_path=DEFAULT_SETTINGS.db_path, settings: Settings | None = None):
        self.settings = settings or DEFAULT_SETTINGS
        self.conn = open_db(db_path)

    def close(self) -> None:
        """关连接。Windows 下 sqlite 文件被占用会锁住目录，测试/关服务时必须显式关。"""
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- 实体
    def upsert_entity(self, name: str, aliases: list[str] | None = None) -> Entity:
        name = name.strip()
        row = self.conn.execute("SELECT * FROM entity WHERE name = ?", (name,)).fetchone()
        if row:
            ent = self._entity_from_row(row)
            if aliases:
                merged = sorted(set(ent.aliases) | set(aliases))
                if merged != ent.aliases:
                    self.conn.execute(
                        "UPDATE entity SET aliases = ? WHERE id = ?",
                        (json.dumps(merged, ensure_ascii=False), ent.id),
                    )
                    self.conn.commit()
                    ent.aliases = merged
            return ent

        # 别名反查：避免「张工」和「张伟」被拆成两个实体
        for cand in self.conn.execute("SELECT * FROM entity"):
            ent = self._entity_from_row(cand)
            if name in ent.aliases:
                return ent

        ent = Entity(
            id=_eid(name),
            name=name,
            type=infer_entity_type(name),
            aliases=aliases or [],
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO entity (id, name, type, aliases, created_at) VALUES (?,?,?,?,?)",
            (ent.id, ent.name, ent.type, json.dumps(ent.aliases, ensure_ascii=False), ent.created_at),
        )
        self.conn.commit()
        return ent

    @staticmethod
    def _entity_from_row(row) -> Entity:
        return Entity(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            aliases=json.loads(row["aliases"] or "[]"),
            created_at=row["created_at"],
        )

    def get_entity(self, entity_id: str) -> Entity | None:
        row = self.conn.execute("SELECT * FROM entity WHERE id = ?", (entity_id,)).fetchone()
        return self._entity_from_row(row) if row else None

    def all_entities(self) -> list[Entity]:
        return [self._entity_from_row(r) for r in self.conn.execute("SELECT * FROM entity ORDER BY name")]

    # ---------------------------------------------------------------- 消息
    def add_message(self, msg: Message) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO message (id, channel, speaker, ts, text, thread) VALUES (?,?,?,?,?,?)",
            (msg.id, msg.channel, msg.speaker, msg.ts, msg.text, msg.thread),
        )
        self.conn.commit()

    def get_message(self, message_id: str) -> Message | None:
        row = self.conn.execute("SELECT * FROM message WHERE id = ?", (message_id,)).fetchone()
        if not row:
            return None
        return Message(
            id=row["id"], channel=row["channel"], speaker=row["speaker"],
            ts=row["ts"], text=row["text"], thread=row["thread"],
        )

    def message_assertions(self, message_id: str) -> list[Assertion]:
        rows = self.conn.execute(
            "SELECT * FROM assertion WHERE source_message_id = ? ORDER BY predicate", (message_id,)
        )
        return [Assertion.from_row(r) for r in rows]

    # ---------------------------------------------------------------- 断言
    def _index(self, a: Assertion) -> None:
        self.conn.execute("DELETE FROM term_index WHERE assertion_id = ?", (a.id,))
        weighted: dict[str, float] = {}
        for t in bigrams(a.entity_name):
            weighted[t] = max(weighted.get(t, 0.0), 1.5)
        for t in bigrams(a.predicate):
            weighted[t] = max(weighted.get(t, 0.0), 1.2)
        for t in bigrams(a.value):
            weighted[t] = max(weighted.get(t, 0.0), 1.0)
        self.conn.executemany(
            "INSERT OR REPLACE INTO term_index (term, assertion_id, weight) VALUES (?,?,?)",
            [(t, a.id, w) for t, w in weighted.items()],
        )

    def slot_assertions(self, slot: str) -> list[Assertion]:
        rows = self.conn.execute(
            "SELECT * FROM assertion WHERE slot = ? ORDER BY COALESCE(valid_from, created_at)", (slot,)
        )
        return [Assertion.from_row(r) for r in rows]

    def active_in_slot(self, slot: str) -> list[Assertion]:
        rows = self.conn.execute(
            "SELECT * FROM assertion WHERE slot = ? AND status IN ('active','disputed') "
            "ORDER BY COALESCE(valid_from, created_at)",
            (slot,),
        )
        return [Assertion.from_row(r) for r in rows]

    def get_assertion(self, aid: str) -> Assertion | None:
        row = self.conn.execute("SELECT * FROM assertion WHERE id = ?", (aid,)).fetchone()
        return Assertion.from_row(row) if row else None

    def _set_status(self, a: Assertion, status: str, note: str = "") -> Assertion:
        if status != a.status and status not in TRANSITIONS.get(a.status, set()):
            note = (note + f" [illegal-transition {a.status}->{status}]").strip()
        a.status = status          # type: ignore[assignment]
        a.note = note
        a.updated_at = now_iso()
        self._save(a)
        return a

    def _save(self, a: Assertion) -> None:
        row = a.to_row()
        self.conn.execute(
            """UPDATE assertion SET entity_id=?, entity_name=?, predicate=?, value=?, slot=?,
               source_message_id=?, source_kind=?, source_quote=?, confidence=?, status=?,
               valid_from=?, valid_to=?, supersedes=?, chain_root=?, extractor=?, pinned=?,
               is_change=?, updated_at=?, note=? WHERE id=?""",
            (
                row["entity_id"], row["entity_name"], row["predicate"], row["value"], row["slot"],
                row["source_message_id"], row["source_kind"], row["source_quote"], row["confidence"],
                row["status"], row["valid_from"], row["valid_to"], row["supersedes"], row["chain_root"],
                row["extractor"], row["pinned"], row["is_change"], row["updated_at"], row["note"],
                row["id"],
            ),
        )
        self._index(a)
        self.conn.commit()

    def write_assertion(self, a: Assertion) -> tuple[Assertion, str]:
        """写入一条断言，返回 (落库后的断言, 动作)。

        动作取值：inserted / corroborated / superseded_old / disputed / duplicated
        """
        slot = a.slot or f"{a.entity_id}::{a.predicate}"

        # 1) 完全同值且仍有效 -> 合并证据
        same = self.conn.execute(
            "SELECT * FROM assertion WHERE slot=? AND value=? AND status IN ('active','disputed')",
            (slot, a.value),
        ).fetchone()
        if same:
            old = Assertion.from_row(same)
            merged = 1.0 - (1.0 - old.confidence) * (1.0 - a.confidence * 0.8)
            old.confidence = round(min(0.99, merged), 4)
            old.updated_at = now_iso()
            old.note = (old.note + f" | +证据({a.source_message_id})").strip(" |")
            self._save(old)
            return old, "corroborated"

        live = self.active_in_slot(slot)

        # 2) 与人工确认值冲突 -> 只能进 disputed
        pinned_conflict = [x for x in live if x.pinned and x.value != a.value]
        if pinned_conflict:
            a.status = "disputed"
            a.note = f"与人工确认值冲突：{pinned_conflict[0].value}（{pinned_conflict[0].id}）"
            a.confidence = min(a.confidence, 0.55)
            self._insert(a)
            return a, "disputed"

        # 2.5) 置信度明显低于现值 -> 不许静默覆盖。
        # 这是「周报滞后转述打不过群里的实时变更」在数据层的落点：
        # 时间更晚不等于更对，证据强度不够就只能挂起等人看。
        weaker = [
            x for x in live
            if x.value != a.value and not a.is_change and a.confidence < x.confidence - 0.06
        ]
        if weaker:
            a.status = "disputed"
            a.note = f"证据强度({a.confidence:.2f})低于现值({weaker[0].confidence:.2f}，{weaker[0].value})，待确认"
            self._insert(a)
            return a, "disputed"

        # 3) 时间窗无法判定 -> 双方 disputed，交给 resolve 输出并列
        #    判据：48 小时内出现两个不同值、置信度接近、且都不是显式变更表述。
        #    显式变更（「改成/转给/延期到」）不进这条，否则正常演进也会被卡住。
        ambiguous = [
            x for x in live
            if x.value != a.value
            and not a.is_change
            and abs(x.confidence - a.confidence) < 0.10
            and _close_in_time(x, a, hours=48)
        ]
        if ambiguous:
            a.status = "disputed"
            a.note = "48 小时内出现不同取值且置信度接近，无法自动判定"
            self._insert(a)
            for x in ambiguous:
                if x.status == "active":
                    self._set_status(x, "disputed", "48 小时内出现不同取值且置信度接近，无法自动判定")
            return a, "disputed"

        # 4) 正常演进：旧值降权保留
        chain_root = a.chain_root or (live[-1].chain_root if live else None) or _nid("c")
        a.chain_root = chain_root
        if live:
            newest = live[-1]
            a.supersedes = newest.id
            if a.valid_from is None:
                a.valid_from = a.created_at
            for x in live:
                x.confidence = round(max(0.05, x.confidence * 0.5), 4)
                self._set_status(x, "superseded", f"被 {a.value} 取代")
                # 旧版本的有效期收口到新版本起点
                x.valid_to = a.valid_from
                self._save(x)
        self._insert(a)
        return a, ("superseded_old" if live else "inserted")

    def _insert(self, a: Assertion) -> None:
        row = a.to_row()
        self.conn.execute(
            """INSERT INTO assertion (id, entity_id, entity_name, predicate, value, slot,
               source_message_id, source_kind, source_quote, confidence, status, valid_from,
               valid_to, supersedes, chain_root, extractor, pinned, is_change, created_at,
               updated_at, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row["id"], row["entity_id"], row["entity_name"], row["predicate"], row["value"],
                row["slot"], row["source_message_id"], row["source_kind"], row["source_quote"],
                row["confidence"], row["status"], row["valid_from"], row["valid_to"],
                row["supersedes"], row["chain_root"], row["extractor"], row["pinned"],
                row["is_change"], row["created_at"], row["updated_at"], row["note"],
            ),
        )
        self._index(a)
        self.conn.commit()

    # ---------------------------------------------------------------- 状态维护
    def sweep_expired(self, now: datetime | None = None) -> list[Assertion]:
        """把越过 valid_to 的 active 断言置为 expired。惰性调用，不做后台定时器。"""
        now = now or datetime.now(timezone.utc)
        expired: list[Assertion] = []
        for row in self.conn.execute("SELECT * FROM assertion WHERE status='active' AND valid_to IS NOT NULL"):
            a = Assertion.from_row(row)
            vt = parse_iso(a.valid_to)
            if vt and vt < now:
                expired.append(self._set_status(a, "expired", "有效期已过"))
        return expired

    def chain(self, slot: str) -> list[Assertion]:
        return self.slot_assertions(slot)

    # ---------------------------------------------------------------- 查询
    def all_assertions(self, status: str | None = None) -> list[Assertion]:
        sql = "SELECT * FROM assertion"
        args: tuple = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY entity_name, predicate, COALESCE(valid_from, created_at)"
        return [Assertion.from_row(r) for r in self.conn.execute(sql, args)]

    def lookup_terms(self, terms: list[str]) -> dict[str, list[tuple[str, float]]]:
        if not terms:
            return {}
        marks = ",".join("?" * len(terms))
        rows = self.conn.execute(
            f"SELECT term, assertion_id, weight FROM term_index WHERE term IN ({marks})",
            tuple(terms),
        )
        out: dict[str, list[tuple[str, float]]] = {}
        for r in rows:
            out.setdefault(r["assertion_id"], []).append((r["term"], r["weight"]))
        return out

    def stats(self) -> dict:
        def one(sql: str) -> int:
            return int(self.conn.execute(sql).fetchone()[0])

        return {
            "entities": one("SELECT COUNT(*) FROM entity"),
            "messages": one("SELECT COUNT(*) FROM message"),
            "assertions": one("SELECT COUNT(*) FROM assertion"),
            "active": one("SELECT COUNT(*) FROM assertion WHERE status='active'"),
            "superseded": one("SELECT COUNT(*) FROM assertion WHERE status='superseded'"),
            "disputed": one("SELECT COUNT(*) FROM assertion WHERE status='disputed'"),
            "expired": one("SELECT COUNT(*) FROM assertion WHERE status='expired'"),
            "corrections": one("SELECT COUNT(*) FROM correction"),
            "learned_rules": one("SELECT COUNT(*) FROM learned_rule WHERE active=1"),
        }

    # ---------------------------------------------------------------- 修正审计
    def log_correction(self, **kw) -> str:
        cid = _nid("fix")
        self.conn.execute(
            """INSERT INTO correction (id, assertion_id, action, entity_id, predicate, old_value,
               new_value, operator, ts, applied_rule) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                cid, kw.get("assertion_id", ""), kw.get("action", ""), kw.get("entity_id", ""),
                kw.get("predicate", ""), kw.get("old_value", ""), kw.get("new_value", ""),
                kw.get("operator", "anonymous"), now_iso(), kw.get("applied_rule", ""),
            ),
        )
        self.conn.commit()
        return cid

    def log_rejected(self, text: str, reason: str, confidence: float, extractor: str) -> None:
        self.conn.execute(
            "INSERT INTO rejected (id, text, reason, confidence, extractor, ts) VALUES (?,?,?,?,?,?)",
            (_nid("rj"), text, reason, confidence, extractor, now_iso()),
        )
        self.conn.commit()

    def log_run(self, query: str, payload: dict) -> str:
        rid = _nid("run")
        self.conn.execute(
            "INSERT INTO run_log (id, ts, query, payload) VALUES (?,?,?,?)",
            (rid, now_iso(), query, json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()
        return rid


def _opening(ts: str | None) -> str:
    """取时间窗起点；缺失时按「史前」处理，让补录能重排。"""
    return ts or "0000"


def _close_in_time(x: Assertion, a: Assertion, hours: float = 48.0) -> bool:
    """两条断言的发生时间是否太近，近到不足以用「后说的为准」来判断。"""
    tx = parse_iso(x.valid_from or x.created_at)
    ta = parse_iso(a.valid_from or a.created_at)
    if tx is None or ta is None:
        return True
    return abs((ta - tx).total_seconds()) <= hours * 3600
