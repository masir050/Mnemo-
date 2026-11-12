"""上下文成本评测：注入量 vs 全量拼接。

基线定义（两种都算，避免只挑好看的那个）：
  A. 全量消息拼接 —— 把群里所有历史消息原样塞进上下文，这是不用记忆系统时的做法；
  B. 相关事实拼接 —— 只把检索命中的断言全量拼进去（不做预算裁剪/去重/折叠）。

Mnemo 的注入量要同时压过这两条线，才有说服力。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mnemo.db import DEFAULT_DB  # noqa: E402
from mnemo.graph_app import MnemoRunner  # noqa: E402
from mnemo.store import Store  # noqa: E402
from mnemo.tokens import PER_MESSAGE_OVERHEAD, estimate_tokens, memory_tokens  # noqa: E402

QUESTIONS = json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    store = Store(args.db)
    runner = MnemoRunner(store)

    msgs = store.conn.execute("SELECT * FROM message ORDER BY ts").fetchall()
    msg_tokens = [estimate_tokens(m["text"]) + PER_MESSAGE_OVERHEAD for m in msgs]
    full_all = sum(msg_tokens)

    rows = []
    for q in QUESTIONS:
        res = runner.run(q["q"])
        assembled = res.bundle.get("tokens_used", 0.0)
        fact_raw = sum(
            memory_tokens(store.get_assertion(c["assertion_id"])) for c in res.citations
        ) if res.citations else 0.0
        # B 基线：本次消解出的全部现值，不做预算裁剪 / 去重 / 折叠
        fact_full = 0.0
        for f in res.facts:
            cur = f.get("current")
            if not cur:
                continue
            a = store.get_assertion(cur["id"])
            if a is not None:
                fact_full += memory_tokens(a)
        rows.append(
            {
                "q": q["q"],
                "assembled": round(assembled, 1),
                "full_messages": round(full_all, 1),
                "full_facts": round(max(fact_full, fact_raw), 1),
                "ratio_messages": round(assembled / full_all, 3) if full_all else 0,
                "ratio_facts": round(assembled / max(fact_full, fact_raw, 1), 3),
                "facts": len(res.facts),
                "included": res.bundle.get("included"),
                "dropped": res.bundle.get("dropped"),
            }
        )

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"语料规模：{len(msgs)} 条消息 / 全量拼接 {full_all:.0f} tok\n")
    print(f"{'问题':<26}{'注入':>7}{'vs全量消息':>12}{'vs全量事实':>12}{'压缩比(A)':>11}")
    print("-" * 70)
    for r in rows:
        print(
            f"{r['q']:<26}{r['assembled']:>7.0f}"
            f"{r['full_messages']:>12.0f}{r['full_facts']:>12.0f}"
            f"{r['ratio_messages']:>10.1%}"
        )
    avg_a = sum(r["ratio_messages"] for r in rows) / len(rows)
    avg_b = sum(r["ratio_facts"] for r in rows) / len(rows)
    print("-" * 70)
    print(f"{'平均':<26}{'':>7}{'':>12}{'':>12}{avg_a:>10.1%}")
    print(f"\n相对全量事实的平均压缩比：{avg_b:.1%}")
    print(f"结论：注入量为全量消息拼接的 {avg_a:.1%}、全量事实拼接的 {avg_b:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
