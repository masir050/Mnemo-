"""把演示语料灌进记忆库。

用法：
    python scripts/seed.py            # 重建 data/mnemo.db
    python scripts/seed.py --keep     # 追加到现有库（去重靠 message.id）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mnemo.config import DEFAULT_SETTINGS  # noqa: E402
from mnemo.db import DEFAULT_DB  # noqa: E402
from mnemo.extract.pipeline import ExtractorPipeline  # noqa: E402
from mnemo.models import Message  # noqa: E402
from mnemo.store import Store  # noqa: E402

CORPUS = ROOT / "data" / "corpus.jsonl"


def load_corpus(path: Path) -> list[Message]:
    msgs: list[Message] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        d = json.loads(line)
        msgs.append(
            Message(
                id=d["id"], channel=d["channel"], speaker=d.get("speaker", ""),
                ts=d["ts"], text=d["text"], thread=d.get("thread", ""),
            )
        )
    return msgs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--keep", action="store_true", help="不清库，追加写入")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    db_path = args.db
    if not args.keep and Path(db_path).exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(db_path + suffix)
            if p.exists():
                p.unlink()

    store = Store(db_path, DEFAULT_SETTINGS)
    pipe = ExtractorPipeline(store)
    msgs = load_corpus(Path(args.corpus))
    results = pipe.ingest_batch(msgs)
    expired = store.sweep_expired()

    stats = store.stats()
    rule_hits = sum(r.by_layer["rule"] for r in results)
    llm_hits = sum(r.by_layer["llm"] for r in results)
    rejected = sum(r.by_layer["rejected"] for r in results)

    if not args.quiet:
        print(f"语料 {len(msgs)} 条 -> 断言 {rule_hits + llm_hits} 条"
              f"（规则 {rule_hits} / 兜底 {llm_hits} / 丢弃 {rejected}）")
        print(f"实体 {stats['entities']}｜active {stats['active']}｜"
              f"superseded {stats['superseded']}｜disputed {stats['disputed']}｜"
              f"expired {stats['expired']}")
        if expired:
            print(f"本次过期 {len(expired)} 条")
        print()
        print("各实体当前记忆：")
        for ent in store.all_entities():
            rows = store.conn.execute(
                "SELECT predicate, value, confidence, status FROM assertion "
                "WHERE entity_id = ? AND status IN ('active','disputed') "
                "ORDER BY predicate",
                (ent.id,),
            ).fetchall()
            if not rows:
                continue
            print(f"  {ent.name} ({ent.type})")
            for r in rows:
                mark = "!" if r["status"] == "disputed" else " "
                print(f"   {mark} {r['predicate']:<12} {r['value']}  "
                      f"[{r['status']}, 置信 {r['confidence']:.2f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
