"""记忆模型：版本链、状态机、证据合并、冲突挂起。"""
from __future__ import annotations

from mnemo.models import Assertion


def _a(store, entity, predicate, value, *, ts, confidence=0.9, is_change=False, kind="im", pinned=False):
    ent = store.upsert_entity(entity)
    return Assertion(
        id=f"a_{entity}_{predicate}_{value}_{ts[-8:]}",
        entity_id=ent.id, entity_name=ent.name, predicate=predicate, value=value,
        slot=f"{ent.id}::{predicate}", source_message_id="m_x", source_kind=kind,
        source_quote="", confidence=confidence, valid_from=ts, is_change=is_change, pinned=pinned,
    )


def test_supersede_keeps_old_version_downweighted(store):
    old = _a(store, "支付网关", "owner", "张伟", ts="2026-10-08T09:00:00+08:00", confidence=0.9)
    store.write_assertion(old)

    new = _a(store, "支付网关", "owner", "李强", ts="2026-10-20T09:00:00+08:00",
             confidence=0.9, is_change=True)
    saved, action = store.write_assertion(new)

    assert action == "superseded_old"
    versions = store.chain(new.slot)
    assert [v.value for v in versions] == ["张伟", "李强"]
    assert versions[0].status == "superseded"
    assert versions[1].status == "active"
    # 旧值降权保留，而不是删除
    assert versions[0].confidence < 0.9 * 0.6
    assert versions[1].supersedes == versions[0].id
    # 有效期收口到新版本起点
    assert versions[0].valid_to == new.valid_from


def test_same_value_corroborates_instead_of_new_version(store):
    a1 = _a(store, "支付网关", "deadline", "2026-11-25", ts="2026-10-21T09:00:00+08:00", confidence=0.8)
    store.write_assertion(a1)
    a2 = _a(store, "支付网关", "deadline", "2026-11-25", ts="2026-11-10T09:00:00+08:00",
            confidence=0.8, kind="ticket")
    saved, action = store.write_assertion(a2)

    assert action == "corroborated"
    assert saved.id == a1.id
    assert saved.confidence > 0.8
    assert len(store.chain(a1.slot)) == 1


def test_pinned_value_blocks_silent_overwrite(store):
    a = _a(store, "结算平台", "owner", "陈磊", ts="2026-10-10T09:00:00+08:00", confidence=1.0, pinned=True)
    store.write_assertion(a)
    b = _a(store, "结算平台", "owner", "王敏", ts="2026-10-20T09:00:00+08:00", confidence=0.95, is_change=True)
    saved, action = store.write_assertion(b)

    assert action == "disputed"
    assert saved.status == "disputed"
    assert store.get_assertion(a.id).status == "active"


def test_close_in_time_conflict_goes_disputed(store):
    a = _a(store, "结算平台", "deadline", "2026-11-30", ts="2026-10-22T09:00:00+08:00", confidence=0.87)
    store.write_assertion(a)
    b = _a(store, "结算平台", "deadline", "2026-11-18", ts="2026-10-22T10:00:00+08:00", confidence=0.87)
    saved, action = store.write_assertion(b)

    assert action == "disputed"
    live = store.active_in_slot(a.slot)
    assert len(live) == 2
    assert {x.status for x in live} == {"disputed"}


def test_weaker_evidence_cannot_overwrite(store):
    a = _a(store, "支付网关", "owner", "李强", ts="2026-10-20T09:00:00+08:00", confidence=0.92, is_change=True)
    store.write_assertion(a)
    # 十天后的周报还在写旧负责人：时间更晚，但证据更弱
    b = _a(store, "支付网关", "owner", "张伟", ts="2026-10-30T09:00:00+08:00",
           confidence=0.80, kind="weekly")
    saved, action = store.write_assertion(b)

    assert action == "disputed"
    assert store.get_assertion(a.id).status == "active"


def test_explicit_change_beats_time_proximity_rule(store):
    """显式变更不受「48 小时内无法判定」限制，否则正常交接会被卡住。"""
    a = _a(store, "对账任务", "schedule", "每周二", ts="2026-10-20T09:00:00+08:00", confidence=0.85)
    store.write_assertion(a)
    b = _a(store, "对账任务", "schedule", "每周四", ts="2026-10-20T10:00:00+08:00",
           confidence=0.85, is_change=True)
    _, action = store.write_assertion(b)

    assert action == "superseded_old"


def test_sweep_expired(store):
    a = _a(store, "交接手册", "system", "wiki", ts="2026-01-01T09:00:00+08:00", confidence=0.8)
    store.write_assertion(a)
    a.valid_to = "2026-02-01T00:00:00+08:00"
    store._save(a)

    expired = store.sweep_expired()
    assert [x.id for x in expired] == [a.id]
    assert store.get_assertion(a.id).status == "expired"


def test_illegal_transition_is_recorded_not_raised(store):
    a = _a(store, "支付网关", "owner", "张伟", ts="2026-10-08T09:00:00+08:00")
    store.write_assertion(a)
    a.status = "superseded"
    store._save(a)
    # superseded -> disputed 不合法，应被记录在 note 里而不是抛异常
    out = store._set_status(a, "disputed")
    assert "illegal-transition" in out.note
