"""组装：预算硬约束、按实体分组、去重折叠。"""
from __future__ import annotations

from mnemo.assemble import assemble
from mnemo.conflict import resolve_candidates
from mnemo.models import Candidate
from mnemo.retrieve import parse_query, retrieve
from tests.conftest import msg


def _seed(pipeline):
    seq = [
        ("a1", "支付网关的负责人是张伟。", "2026-10-08T09:00:00+08:00"),
        ("a2", "支付网关的截止时间是2026-11-15。", "2026-10-09T09:00:00+08:00"),
        ("a3", "支付网关的备份是李强。", "2026-10-10T09:00:00+08:00"),
        ("a4", "支付网关的地址是 gw.prod。", "2026-10-11T09:00:00+08:00"),
        ("a5", "支付网关的负责人改成李强了。", "2026-10-20T09:00:00+08:00"),
        ("a6", "支付网关的风险是回滚没演练过。", "2026-10-21T09:00:00+08:00"),
    ]
    for mid, text, ts in seq:
        pipeline.ingest(msg(mid, text, ts=ts))


def test_budget_is_hard_cap(pipeline):
    _seed(pipeline)
    q = parse_query(pipeline.store, "支付网关")
    cands = retrieve(pipeline.store, q)
    facts = resolve_candidates(pipeline.store, cands)
    bundle = assemble(cands, facts, budget=60)
    assert bundle.tokens_used <= 60
    assert bundle.dropped, "预算不足时必须有被丢弃的条目"


def test_one_value_per_slot(pipeline):
    _seed(pipeline)
    q = parse_query(pipeline.store, "支付网关")
    facts = resolve_candidates(pipeline.store, retrieve(pipeline.store, q))
    bundle = assemble([], facts, budget=800)
    owner_lines = [l for l in bundle.lines if "- 负责人：" in l]
    assert len(owner_lines) == 1, bundle.text()


def test_entity_header_rendered_once(pipeline):
    _seed(pipeline)
    q = parse_query(pipeline.store, "支付网关")
    facts = resolve_candidates(pipeline.store, retrieve(pipeline.store, q))
    bundle = assemble([], facts, budget=800)
    counts = [l for l in bundle.lines if l.startswith("### 支付网关")]
    assert len(counts) == 1
    # 除实体头外，其余行不应再重复实体名（省 token 的关键）
    body = [l for l in bundle.lines if not l.startswith("### ")]
    assert not any("支付网关" in l for l in body)


def test_conflict_marked_in_context(pipeline):
    pipeline.ingest(msg("c1", "结算平台的截止时间是2026-11-30。", ts="2026-10-22T09:00:00+08:00"))
    pipeline.ingest(msg("c2", "结算平台的截止时间是2026-11-18。", ts="2026-10-22T10:00:00+08:00"))
    q = parse_query(pipeline.store, "结算平台截止时间")
    facts = resolve_candidates(pipeline.store, retrieve(pipeline.store, q))
    bundle = assemble([], facts, budget=800)
    assert any("需确认" in l for l in bundle.lines), bundle.text()


def test_history_collapsed_to_one_line(pipeline):
    _seed(pipeline)
    q = parse_query(pipeline.store, "支付网关负责人")
    facts = resolve_candidates(pipeline.store, retrieve(pipeline.store, q))
    bundle = assemble([], facts, budget=800)
    assert any("变更：" in l for l in bundle.lines)
    assert bundle.utilization <= 1.0


def test_empty_input_is_safe():
    bundle = assemble([], [], budget=100)
    assert bundle.tokens_used < 100
    assert bundle.included == []


def test_candidate_fallback_when_no_facts():
    """facts 为空时退回按分数取候选，不至于给出空上下文。"""
    from mnemo.store import Store
    from mnemo.config import DEFAULT_SETTINGS
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as d:
        with Store(pathlib.Path(d) / "x.db", DEFAULT_SETTINGS) as store:
            store.upsert_entity("支付网关")
            a = Candidate(assertion=_dummy("支付网关"), relevance=0.8, score=0.7)
            bundle = assemble([a], [], budget=200)
            assert bundle.included


def _dummy(entity):
    from mnemo.models import Assertion

    return Assertion(
        id="a_x", entity_id="e_x", entity_name=entity, predicate="owner", value="张伟",
        slot="e_x::owner", source_message_id="m", source_kind="im", source_quote="",
        confidence=0.8,
    )
