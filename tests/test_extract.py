"""抽取：规则命中、值清洗、日期归一、分层兜底。"""
from __future__ import annotations

import pytest

from mnemo.extract.llm import OfflineExtractor
from mnemo.extract.rules import clean_entity, clean_value, match_rules, normalize_date
from mnemo.extract.segment import analyse, split_sentences


@pytest.mark.parametrize(
    "text,entity,predicate,value",
    [
        ("支付网关的负责人是张伟，这周先接监控。", "支付网关", "owner", "张伟"),
        ("支付网关的负责人改成李强了。", "支付网关", "owner", "李强"),
        ("支付网关由李强负责。", "支付网关", "owner", "李强"),
        ("支付网关的备份是王敏。", "支付网关", "backup", "王敏"),
        ("支付网关的截止时间是2026-11-15。", "支付网关", "deadline", "2026-11-15"),
        ("支付网关的截止时间延期到2026-11-25。", "支付网关", "deadline", "2026-11-25"),
        ("结算平台的状态是联调中。", "结算平台", "status", "联调中"),
        ("对账任务的风险是上次跑挂了没人知道。", "对账任务", "risk", "上次跑挂了没人知道"),
        ("对账任务的操作流程是先跑precheck脚本。", "对账任务", "procedure", "先跑precheck脚本"),
        ("数据治理专项的值班是周四上午。", "数据治理专项", "schedule", "周四上午"),
        ("支付网关的地址是 gw.internal.prod。", "支付网关", "system", "gw.internal.prod"),
    ],
)
def test_rule_hits(text, entity, predicate, value):
    hits = match_rules(text)
    got = {(h.entity, h.predicate, h.value) for h in hits}
    assert (entity, predicate, value) in got, f"未命中：{got}"


def test_greedy_entity_not_eating_predicate_word():
    """「操作流程」不能被贪婪的实体段吃成实体「操作」。"""
    hits = match_rules("对账任务的操作流程是先跑precheck脚本。")
    assert hits and hits[0].entity == "对账任务"


def test_entity_prefix_noise_is_stripped():
    hits = match_rules("张伟说支付网关的负责人是李强")
    assert hits and hits[0].entity == "支付网关"


def test_change_marker_raises_confidence():
    plain = match_rules("支付网关的负责人是李强")[0]
    change = match_rules("支付网关的负责人改成李强")[0]
    assert change.is_change and change.confidence > plain.confidence


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026年11月20日", "2026-11-20"),
        ("2026/11/20", "2026-11-20"),
        ("11月20日", "2026-11-20"),
        ("2026-11-20", "2026-11-20"),
        ("下周三", None),
    ],
)
def test_normalize_date(raw, expected):
    assert normalize_date(raw, default_year=2026) == expected


def test_clean_value_trims_particles():
    assert clean_value("李强了", "person") == "李强"
    assert clean_value("李强那边", "text") == "李强"
    assert clean_value("  先跑脚本  ", "text") == "先跑脚本"


def test_clean_entity_strips_coordination():
    assert clean_entity("结算平台和支付网关") == "支付网关"


def test_sentence_split():
    parts = split_sentences("支付网关的负责人是张伟。结算平台的状态是联调中！对账任务呢？")
    assert len(parts) == 3


def test_dependency_frame():
    f = analyse("支付网关的负责人是张伟")
    assert f.subject == "支付网关" and f.predicate == "负责人" and f.obj == "张伟"

    f2 = analyse("由李强负责支付网关")
    assert f2.obj == "李强"


def test_offline_fallback_catches_generic_pattern():
    """规则表里没有「归…管」这种说法，交给兜底通道，置信度压在 0.55 以下。"""
    hits = OfflineExtractor().extract("支付网关归李强管")
    assert hits and hits[0].predicate == "owner"
    assert hits[0].confidence <= 0.55


def test_offline_fallback_returns_nothing_on_chitchat():
    assert OfflineExtractor().extract("中午吃什么？") == []


def test_pipeline_drops_chitchat(pipeline):
    from tests.conftest import msg

    r = pipeline.ingest(msg("c1", "中午吃什么？"))
    assert r.assertions == []


def test_pipeline_writes_assertion_with_source(pipeline, store):
    from tests.conftest import msg

    r = pipeline.ingest(msg("c2", "支付网关的负责人是张伟。", ts="2026-10-08T09:00:00+08:00"))
    assert len(r.assertions) == 1
    a = r.assertions[0]
    assert a.source_message_id == "c2"
    assert a.source_quote
    assert a.extractor.startswith("rule:")
    assert store.get_message("c2") is not None


def test_source_weight_affects_confidence(tmp_path):
    from mnemo.extract.pipeline import ExtractorPipeline
    from mnemo.store import Store
    from tests.conftest import msg

    text = "支付网关的截止时间是2026-11-15。"
    ts = "2026-10-09T09:00:00+08:00"

    p_im = ExtractorPipeline(Store(tmp_path / "im.db"))
    a_im = p_im.ingest(msg("s1", text, ts=ts, channel="im")).assertions[0]

    p_tk = ExtractorPipeline(Store(tmp_path / "tk.db"))
    a_tk = p_tk.ingest(msg("s2", text, ts=ts, channel="ticket")).assertions[0]

    assert a_tk.confidence > a_im.confidence
