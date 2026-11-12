"""终端演示：一遍看清「现值 / 轨迹 / 冲突 / 纠错闭环」。

用临时副本跑，不动 data/mnemo.db，所以可以反复执行。
    python scripts/demo.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mnemo.config import DEFAULT_SETTINGS   # noqa: E402
from mnemo.extract.pipeline import ExtractorPipeline  # noqa: E402
from mnemo.feedback import FeedbackLoop     # noqa: E402
from mnemo.graph_app import MnemoRunner     # noqa: E402
from mnemo.models import Message            # noqa: E402
from mnemo.store import Store               # noqa: E402

SRC_DB = ROOT / "data" / "mnemo.db"


def hr(title: str) -> None:
    print(f"\n{'─' * 66}\n{title}\n{'─' * 66}")


def ask(runner: MnemoRunner, q: str) -> None:
    res = runner.run(q)
    print(f"\n问：{q}")
    print(res.answer)
    print(f"  （注入 {res.bundle['tokens_used']} tok / 预算 {res.bundle['budget']}，"
          f"引用 {len(res.citations)} 条）")


def main() -> None:
    if not SRC_DB.exists():
        print("先跑 python scripts/seed.py 生成演示数据")
        raise SystemExit(1)

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "demo.db"
        shutil.copy(SRC_DB, db)
        # WAL 里的内容可能还没落盘，复制主库后单独归档会导致读不到最新行；
        # 这里直接把主库当基线（seed 脚本结束时已 checkpoint）。
        with Store(db, DEFAULT_SETTINGS) as store:
            runner = MnemoRunner(store)
            fb = FeedbackLoop(store)

            hr("1. 现在归谁：现值 + 冲突 + 变更轨迹")
            ask(runner, "支付网关现在谁负责？")
            ask(runner, "支付网关的截止时间是什么时候？")

            hr("2. 有分歧的地方，系统不猜，直接把两种说法都摆出来")
            ask(runner, "结算平台的截止时间是几号？")
            fact = next((f for f in runner.run("结算平台的截止时间").facts
                         if f["predicate"] == "deadline"), None)
            assert fact and fact["current"], "演示数据里应有结算平台截止时间"
            wrong = next((a for a in fact["alternates"] if a["value"] != "2026-11-18"), None)
            if wrong is None:
                print("\n（这条已经没有并列值了，跳过纠错演示）")
            else:
                print(f"\n运营判断：{wrong['value']} 是旧的，判为误抽 -> "
                      f"reject({wrong['id']})")
                fb.reject(wrong["id"], "会议已确认改到 11-18", operator="demo")

                hr("3. 判完再问一次：不再需要人工确认")
                ask(runner, "结算平台的截止时间是几号？")

                hr("4. 同一句话重放：错值不会自己长回来")
                pipe = ExtractorPipeline(store, DEFAULT_SETTINGS)
                pipe.refresh_rules()
                mid = wrong["source"].partition(":")[2]
                src = store.get_message(mid)
                if src:
                    again = pipe.ingest(Message(
                        id="replay", channel=src.channel, speaker=src.speaker,
                        ts=src.ts, text=src.text,
                    ))
                    print(f"\n重放：{src.text}")
                    print(f"新增断言 {len(again.assertions)} 条，被约束拦下 {len(again.rejected)} 条")
                    for r in again.rejected:
                        print(f"  拦下：{r['quote']}（{r['rule']}）")

            hr("5. 学了什么：负例与泛化规则都落库，可查可停用")
            for r in fb.list_rules():
                kind = r["template"].get("kind", "?")
                print(f"  [{kind:10s}] {r['pattern']}  pred={r['predicate']}  conf={r['confidence']}")

            hr("6. 记忆面板能看到的审计轨迹")
            for row in store.conn.execute("SELECT action, old_value, new_value, operator FROM correction"):
                print(f"  {row['action']}: {row['old_value']} -> {row['new_value'] or '(空)'}  by {row['operator']}")


if __name__ == "__main__":
    main()
