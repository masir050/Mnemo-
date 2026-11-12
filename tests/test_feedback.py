"""纠错闭环：改一次，系统应当同时留下「现值」「负例」「规则」三样东西，
并且下次同一句话不会再把错值抽回来。"""
from __future__ import annotations

from tests.conftest import msg


def _owner_id(store, entity="支付网关"):
    row = store.conn.execute(
        "SELECT id FROM assertion WHERE entity_name = ? AND predicate = 'owner' "
        "AND status = 'active' ORDER BY COALESCE(valid_from, created_at) DESC LIMIT 1",
        (entity,),
    ).fetchone()
    return row["id"] if row else None


def test_correct_writes_pinned_manual_value(demo, feedback):
    aid = _owner_id(demo.store)
    out = feedback.correct(aid, "陈磊", operator="tester")

    assert out.action == "correct"
    fixed = demo.store.get_assertion(out.assertion_id)
    assert fixed is not None
    assert fixed.value == "陈磊"
    assert fixed.source_kind == "manual"
    assert fixed.pinned is True
    assert fixed.confidence == 1.0


def test_correct_records_negative_and_rule(demo, feedback):
    aid = _owner_id(demo.store)
    out = feedback.correct(aid, "陈磊", operator="tester")

    kinds = {r["template"].get("kind") for r in feedback.list_rules()}
    assert "negative" in kinds, "纠正必须留下负例约束"
    assert out.learned_rule is not None, "纠正必须回写一条可复用规则"


def test_replay_same_sentence_does_not_reintroduce_wrong_value(demo, feedback):
    """闭环的核心断言：判错之后再抽一遍，错的不会回来。"""
    aid = _owner_id(demo.store)
    bad = demo.store.get_assertion(aid)
    assert bad is not None
    feedback.correct(aid, "陈磊", operator="tester")

    demo.refresh_rules()
    again = demo.ingest(msg("x3_replay", bad.source_quote or "支付网关的负责人是李强。",
                            "2026-10-25T09:00:00+08:00"))
    values = [a.value for a in again.assertions]
    assert bad.value not in values or values == [], f"旧错值复现：{values}"


def test_negative_is_scoped_to_the_sentence(demo, feedback):
    """负例绑原句，不能把这个值永久拉黑——人真的换了回来，还得能记上。"""
    aid = _owner_id(demo.store)
    bad = demo.store.get_assertion(aid)
    assert bad is not None
    feedback.correct(aid, "陈磊", operator="tester")

    demo.refresh_rules()
    fresh = demo.ingest(msg("x3_other", f"支付网关的负责人是{bad.value}，下周正式接手。",
                            "2026-11-05T09:00:00+08:00"))
    assert any(a.value == bad.value for a in fresh.assertions), "换了说法的新消息不该被旧负例吃掉"


def test_reject_marks_wrong_record(demo, feedback):
    aid = _owner_id(demo.store)
    out = feedback.reject(aid, "抽错了", operator="tester")
    a = demo.store.get_assertion(out.assertion_id)
    assert a is not None
    assert a.status == "superseded"
    assert a.confidence < 0.5


def test_confirm_pins_and_raises_confidence(demo, feedback):
    aid = _owner_id(demo.store)
    before = demo.store.get_assertion(aid)
    assert before is not None
    out = feedback.confirm(aid, operator="tester")
    after = demo.store.get_assertion(out.assertion_id)
    assert after is not None
    assert after.pinned is True
    assert after.confidence == 1.0
    assert after.confidence >= before.confidence


def test_correction_is_written_to_audit_log(demo, feedback):
    aid = _owner_id(demo.store)
    feedback.correct(aid, "陈磊", operator="tester")
    row = demo.store.conn.execute(
        "SELECT * FROM correction ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["action"] == "correct"
    assert row["operator"] == "tester"


def test_toggle_rule_can_disable_learned_rule(demo, feedback):
    aid = _owner_id(demo.store)
    out = feedback.correct(aid, "陈磊", operator="tester")
    rid = (out.learned_rule or {}).get("id")
    assert rid
    feedback.toggle_rule(rid, False)
    active = {r["id"]: r["active"] for r in feedback.list_rules()}
    assert active[rid] is False


def test_correct_to_same_value_is_confirm(demo, feedback):
    """改成同一个值等于确认；不该顺手记一条负例把这句话否掉。"""
    aid = _owner_id(demo.store)
    cur = demo.store.get_assertion(aid)
    assert cur is not None
    out = feedback.correct(aid, cur.value, operator="tester")
    assert out.action == "confirm"
    assert out.negative_added is False
    assert not [r for r in feedback.list_rules() if r["template"].get("kind") == "negative"]


def test_rejecting_rival_promotes_survivor_back_to_active(demo, feedback):
    """分歧的一方被判掉之后，另一方不该还挂着 disputed —— 否则每次提问都要人再确认一遍。"""
    from mnemo.conflict import resolve_candidates
    from mnemo.retrieve import parse_query, retrieve
    from mnemo.graph_app import MnemoRunner

    runner = MnemoRunner(demo.store)
    fact = next(f for f in runner.run("结算平台的截止时间").facts
                if f["predicate"] == "deadline")
    assert fact["conflict"] and fact["alternates"]

    wrong = next(a for a in fact["alternates"] if a["value"] != "2026-11-18")
    feedback.reject(wrong["id"], "会议确认改到 11-18", operator="tester")

    cands = retrieve(demo.store, parse_query(demo.store, "结算平台的截止时间"))
    after = next(f for f in resolve_candidates(demo.store, cands) if f.predicate == "deadline")
    assert after.current is not None and after.current.value == "2026-11-18"
    assert after.alternates == []
    assert after.conflict is False
    assert after.current.status == "active"
    assert "需确认" not in runner.run("结算平台的截止时间").answer
