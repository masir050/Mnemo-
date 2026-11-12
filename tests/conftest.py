"""测试夹具：每个用例一份独立的 SQLite 文件，互不污染。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 测试一律走离线抽取，不许碰真实模型（.env 里配了 key 也不影响）
os.environ["MNEMO_LLM_PROVIDER"] = "offline"
os.environ.pop("MNEMO_LLM_BASE_URL", None)
os.environ.pop("MNEMO_LLM_API_KEY", None)

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mnemo.config import DEFAULT_SETTINGS  # noqa: E402
from mnemo.extract.pipeline import ExtractorPipeline  # noqa: E402
from mnemo.feedback import FeedbackLoop  # noqa: E402
from mnemo.graph_app import MnemoRunner  # noqa: E402
from mnemo.models import Message  # noqa: E402
from mnemo.store import Store  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "mnemo.db", DEFAULT_SETTINGS)


@pytest.fixture
def pipeline(store):
    return ExtractorPipeline(store)


@pytest.fixture
def feedback(store):
    return FeedbackLoop(store)


@pytest.fixture
def runner(store):
    return MnemoRunner(store)


def msg(mid: str, text: str, ts: str = "2026-10-08T09:00:00+08:00", channel: str = "im", speaker="某人") -> Message:
    return Message(id=mid, channel=channel, speaker=speaker, ts=ts, text=text)


@pytest.fixture
def demo(pipeline):
    """一小段含变更与冲突的对话，供端到端用例使用。"""
    seq = [
        msg("x1", "支付网关的负责人是张伟。", "2026-10-08T09:00:00+08:00"),
        msg("x2", "支付网关的截止时间是2026-11-15。", "2026-10-09T09:00:00+08:00", "ticket"),
        msg("x3", "支付网关的负责人改成李强了。", "2026-10-20T09:00:00+08:00"),
        msg("x4", "支付网关的截止时间延期到2026-11-25。", "2026-10-21T09:00:00+08:00", "ticket"),
        msg("x5", "结算平台的截止时间是2026-11-30。", "2026-10-22T09:00:00+08:00"),
        msg("x6", "结算平台的截止时间是2026-11-18。", "2026-10-22T10:00:00+08:00"),
    ]
    for m in seq:
        pipeline.ingest(m)
    return pipeline
