"""纠错闭环的量化验证：人工修一轮之后，同样的话再说一遍，系统还会不会犯同样的错。

两段互不共享数据库，避免「人工值就摆在库里、现值当然对」这种假阳性：
  第 1 轮  干净库灌全量语料 -> 拿 15 道金标准对答案 -> 记正确率 / 冲突 / 需确认
           模拟运营在面板上干活：答错的槽位直接改；同一槽位里与金标准不一致的并列值判为误抽
  第 2 轮  新建库，先把第 1 轮学到的规则与负例灌进去，再灌同一份语料 -> 再对一次答案

第 2 轮必须只靠「规则」变好，才能说明收敛发生在抽取端。所以复用的是规则表，
不是把人工值搬过去。语料里刻意留了两处真实分歧（结算平台截止日、支付网关负责人），
它们正是负例约束要消灭的东西。

跑法：
    python scripts/eval_convergence.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mnemo.config import DEFAULT_SETTINGS              # noqa: E402
from mnemo.extract.pipeline import ExtractorPipeline    # noqa: E402
from mnemo.feedback import FeedbackLoop                # noqa: E402
from mnemo.graph_app import MnemoRunner                # noqa: E402
from mnemo.models import Message, parse_iso            # noqa: E402
from mnemo.store import Store                          # noqa: E402

CORPUS = ROOT / "data" / "corpus.jsonl"
QUESTIONS = ROOT / "data" / "questions.json"


def load_messages() -> list[Message]:
    out: list[Message] = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        out.append(Message(
            id=d["id"], channel=d.get("channel", "im"), speaker=d.get("speaker", ""),
            ts=d.get("ts", ""), text=d["text"], thread=d.get("thread", ""),
        ))
    out.sort(key=lambda m: parse_iso(m.ts) or parse_iso("1970-01-01T00:00:00+00:00"))
    return out


def copy_rules(src: Store, dst: Store) -> int:
    """把学到的规则搬到新库：生产里规则表随系统走，不随某个库走。"""
    rows = src.conn.execute(
        "SELECT name, pattern, predicate, template, confidence, hits, origin, active, created_at "
        "FROM learned_rule"
    ).fetchall()
    for i, r in enumerate(rows):
        dst.conn.execute(
            """INSERT INTO learned_rule (id, name, pattern, predicate, template, confidence, hits,
               origin, active, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (f"lr_{i:04d}", r["name"], r["pattern"], r["predicate"], r["template"],
             r["confidence"], r["hits"], r["origin"], r["active"], r["created_at"]),
        )
    dst.conn.commit()
    return len(rows)


def ingest_all(store: Store, messages: list[Message]) -> None:
    pipe = ExtractorPipeline(store, DEFAULT_SETTINGS)
    pipe.refresh_rules()
    for m in messages:
        pipe.ingest(m)


def measure(store: Store, questions: list[dict]) -> dict:
    runner = MnemoRunner(store, DEFAULT_SETTINGS)
    details = []
    for q in questions:
        res = runner.run(q["q"])
        fact = next((f for f in res.facts
                     if f["entity"] == q["entity"] and f["predicate"] == q["predicate"]), None) or {}
        cur = fact.get("current") or {}
        value = cur.get("value") or ""
        details.append({
            "q": q["q"], "expect": q["expect"], "got": value,
            "ok": bool(value) and (value == q["expect"] or q["expect"] in value),
            "assertion_id": cur.get("id"),
            "alternates": [a["id"] for a in fact.get("alternates") or []],
            "alts": [a["value"] for a in fact.get("alternates") or []],
            "conflict": bool(fact.get("conflict")),
        })

    counts = {
        s: store.conn.execute("SELECT COUNT(*) c FROM assertion WHERE status = ?", (s,)).fetchone()["c"]
        for s in ("active", "superseded", "disputed", "expired")
    }
    return {
        "accuracy": sum(d["ok"] for d in details) / max(1, len(details)),
        "details": details,
        "counts": counts,
        "need_confirm": sum(1 for d in details if d["conflict"]),
        "rejected": store.conn.execute("SELECT COUNT(*) c FROM rejected").fetchone()["c"],
    }


def main() -> None:
    messages = load_messages()
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # ------------------------------------------------------------------ 第 1 轮
        with Store(tmp / "round1.db", DEFAULT_SETTINGS) as store:
            ingest_all(store, messages)
            r1 = measure(store, questions)

            fb = FeedbackLoop(store, DEFAULT_SETTINGS)
            fixed = rejected = 0
            rejected_msgs: list[str] = []
            for d in r1["details"]:
                if not d["ok"] and d["assertion_id"]:
                    fb.correct(d["assertion_id"], d["expect"], operator="eval")
                    fixed += 1
                # 同一槽位里跟金标准不一致的并列值：运营会直接判误抽
                for alt_id, alt_val in zip(d["alternates"], d["alts"]):
                    if not (alt_val == d["expect"] or d["expect"] in alt_val):
                        src = store.get_assertion(alt_id)
                        fb.reject(alt_id, "与人工确认值不一致", operator="eval")
                        rejected += 1
                        if src and src.source_message_id not in rejected_msgs:
                            rejected_msgs.append(src.source_message_id)

            # 判完立刻重放那几句话：这是「同一个错不再犯第二次」最直接的检查
            pipe = ExtractorPipeline(store, DEFAULT_SETTINGS)
            pipe.refresh_rules()
            replay_new = replay_blocked = 0
            for mid in rejected_msgs:
                src_msg = store.get_message(mid)
                if src_msg is None:
                    continue
                again = pipe.ingest(Message(
                    id=f"{mid}_replay", channel=src_msg.channel, speaker=src_msg.speaker,
                    ts=src_msg.ts, text=src_msg.text, thread=src_msg.thread,
                ))
                replay_new += len(again.assertions)
                replay_blocked += len(again.rejected)

            rules = [r for r in fb.list_rules() if r["active"]]
            rule_desc = sorted({f"{r['name']}" for r in rules})

            # ------------------------------------------------------------------ 第 2 轮
            with Store(tmp / "round2.db", DEFAULT_SETTINGS) as warm:
                copied = copy_rules(store, warm)
                ingest_all(warm, messages)
                r2 = measure(warm, questions)

    def line(tag: str, r: dict) -> str:
        c = r["counts"]
        return (f"{tag}  命中率 {r['accuracy'] * 100:5.1f}%   有效 {c['active']:2d}   "
                f"冲突 {c['disputed']:2d}   带⚠答案 {r['need_confirm']:2d}   "
                f"低置信拦截 {r['rejected']:2d}")

    print(f"语料 {len(messages)} 条消息，金标准 {len(questions)} 题")
    print("-" * 74)
    print(line("首轮  ", r1))
    print(line("修正后", r2))
    print("-" * 74)
    print(f"人工修正 {fixed} 条 + 判误抽 {rejected} 条 -> 学到 {copied} 条规则/约束")
    for name in rule_desc:
        print(f"    · {name}")
    print(f"重放被判否定的 {len(rejected_msgs)} 句话：新增断言 {replay_new} 条，"
          f"被约束拦下 {replay_blocked} 条")
    print(f"金标准命中率 {r1['accuracy'] * 100:.1f}% -> {r2['accuracy'] * 100:.1f}%，"
          f"冲突 {r1['counts']['disputed']} -> {r2['counts']['disputed']}，"
          f"需人工确认 {r1['need_confirm']} -> {r2['need_confirm']} 题")

    still = [d for d in r2["details"] if not d["ok"]]
    if still:
        print("\n仍未命中：")
        for d in still:
            print(f"  - {d['q']}  期望 {d['expect']}，实得 {d['got'] or '(无)'}")


if __name__ == "__main__":
    main()
