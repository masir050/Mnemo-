"""同一批金标准问题，比「固定流水线」与「Agent 循环」的命中率、token、延迟。

    python scripts/eval_agent.py            # 全量 15 题
    python scripts/eval_agent.py --limit 5  # 先跑几题看效果
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mnemo.agent import AgentRunner, build_model        # noqa: E402
from mnemo.config import DEFAULT_SETTINGS              # noqa: E402
from mnemo.graph_app import MnemoRunner                # noqa: E402
from mnemo.store import Store                          # noqa: E402

QUESTIONS = ROOT / "data" / "questions.json"
DB = ROOT / "data" / "mnemo.db"


def hit(expect: str, text: str) -> bool:
    return bool(expect) and (expect in (text or ""))


def run(limit: int | None = None) -> None:
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    if limit:
        questions = questions[:limit]

    store = Store(DB, DEFAULT_SETTINGS)
    pipe = MnemoRunner(store)
    agent = AgentRunner(store, build_model(store.settings))

    rows = []
    t_pipe = t_agent = 0
    tok_pipe = tok_agent = 0
    ok_pipe = ok_agent = 0

    for q in questions:
        t0 = time.perf_counter()
        res = pipe.run(q["q"])
        ms_p = int((time.perf_counter() - t0) * 1000)
        fact = next((f for f in res.facts
                     if f["entity"] == q["entity"] and f["predicate"] == q["predicate"]), None) or {}
        got_p = ((fact.get("current") or {}).get("value") or "")
        p_ok = hit(q["expect"], got_p)

        r = agent.run(q["q"])
        ms_a = r.elapsed_ms
        a_ok = hit(q["expect"], r.answer) or any(hit(q["expect"], c["value"]) for c in r.citations)

        ok_pipe += p_ok
        ok_agent += a_ok
        t_pipe += ms_p
        t_agent += ms_a
        tok_agent += int(r.usage.get("total_tokens") or 0)
        rows.append((q["q"], p_ok, a_ok, got_p[:18], r.tool_calls, ms_p, ms_a, r.stop_reason))

    print(f"{'问题':32s} {'流水线':>5s} {'Agent':>6s} {'工具':>4s} {'流水线ms':>8s} {'Agent ms':>8s}  停止")
    print("-" * 92)
    for q, p, a, got, tools, mp, ma, stop in rows:
        print(f"{q[:30]:32s} {'✓' if p else '·':>5s} {'✓' if a else '·':>6s} "
              f"{tools:>4d} {mp:>8d} {ma:>8d}  {stop}")
    n = len(rows)
    print("-" * 92)
    print(f"命中   流水线 {ok_pipe}/{n} = {ok_pipe / n:.1%}    Agent {ok_agent}/{n} = {ok_agent / n:.1%}")
    print(f"平均延迟  流水线 {t_pipe / n / 1000:.2f}s    Agent {t_agent / n / 1000:.2f}s")
    print(f"Agent 平均 token {tok_agent / n:.0f}/题（流水线不调模型，0）")
    store.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    run(ap.parse_args().limit)
