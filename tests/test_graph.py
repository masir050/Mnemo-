"""编排层：节点顺序、分支、流式事件、成文规则。"""
from __future__ import annotations

from mnemo.graph_app import GRAPH_BACKEND, compose_answer, context_overhead
from tests.conftest import msg


def _stages(result) -> list[str]:
    return [e["stage"] for e in result.trace]


def test_run_returns_answer_and_trace(demo, runner):
    res = runner.run("支付网关现在谁负责？")
    assert res.answer
    assert res.facts
    stages = _stages(res)
    for expected in ("parse", "retrieve", "resolve", "assemble", "compose"):
        assert expected in stages, f"缺少阶段 {expected}：{stages}"


def test_answer_contains_present_value(demo, runner):
    res = runner.run("支付网关现在谁负责？")
    assert "李强" in res.answer


def test_history_is_reported_as_track(demo, runner):
    res = runner.run("支付网关负责人")
    assert "变更轨迹" in res.answer


def test_conflict_is_surfaced_not_hidden(demo, runner):
    res = runner.run("结算平台的截止时间")
    assert "需确认" in res.answer
    assert "2026-11-30" in res.answer and "2026-11-18" in res.answer


def test_citations_trace_back_to_source_message(demo, runner):
    res = runner.run("支付网关现在谁负责？")
    assert res.citations
    for c in res.citations:
        kind, _, mid = c["source"].partition(":")
        assert kind in {"im", "ticket", "weekly", "manual", "llm", "doc"}
        assert demo.store.get_message(mid) is not None, f"引用指向不存在的消息 {mid}"


def test_no_hit_returns_apology_not_hallucination(demo, runner):
    res = runner.run("量子咖啡机的保修期是多久？")
    assert "没有检索到" in res.answer
    assert res.citations == []


def test_context_stays_within_budget(demo, runner):
    res = runner.run("支付网关")
    assert res.bundle["tokens_used"] <= res.bundle["budget"]
    assert 0 < res.bundle["utilization"] <= 1.0


def test_stream_emits_all_stages_then_final(demo, runner):
    events = list(runner.stream("支付网关现在谁负责？"))
    stages = [e.stage for e in events]
    assert stages[-1] == "final"
    for expected in ("parse", "retrieve", "resolve", "assemble", "compose"):
        assert expected in stages, f"SSE 缺少阶段 {expected}：{stages}"
    final = events[-1].payload
    assert final["answer"]
    assert final["bundle"]["tokens_used"] > 0


def test_widen_branch_fires_when_recall_is_thin(demo, runner):
    """只提到一个偏僻词的查询，召回不足应触发一次放宽重检，而不是直接空手而归。"""
    events = list(runner.stream("对账任务"))
    widen = [e for e in events if e.stage == "widen"]
    assert widen, "召回不足时应走 widen 分支"
    retries = [e for e in events if e.stage == "retrieve"]
    assert len(retries) >= 2, "widen 之后应回到 retrieve 再检一次"


def test_run_is_logged(demo, runner):
    runner.run("支付网关现在谁负责？")
    row = demo.store.conn.execute("SELECT * FROM run_log ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row is not None
    assert row["query"] == "支付网关现在谁负责？"


def test_backend_is_reported(demo, runner):
    res = runner.run("支付网关")
    assert res.backend == GRAPH_BACKEND


# --------------------------------------------------------------------- 成文单测
def test_compose_answer_hides_low_confidence_facts(demo):
    from mnemo.conflict import resolve_candidates
    from mnemo.retrieve import parse_query, retrieve

    cands = retrieve(demo.store, parse_query(demo.store, "支付网关"))
    facts = resolve_candidates(demo.store, cands)
    answer, cites = compose_answer("支付网关", facts, ["支付网关"])
    assert answer
    assert len(cites) == len([f for f in facts if f.current and f.confidence >= 0.5][:6])


def test_context_overhead_is_measured_against_full_text(demo):
    from mnemo.conflict import resolve_candidates
    from mnemo.retrieve import parse_query, retrieve

    cands = retrieve(demo.store, parse_query(demo.store, "支付网关"))
    facts = resolve_candidates(demo.store, cands)
    overhead = context_overhead(facts, 40.0)
    assert overhead["assembled"] == 40.0
    assert 0 <= overhead["ratio"] <= 1.0
