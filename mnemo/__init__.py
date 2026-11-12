"""Mnemo —— 面向岗位交接的组织记忆系统。

    Store / ExtractorPipeline  写入与抽取
    MnemoRunner                检索、消解、组装、成文
    FeedbackLoop               人工纠错与规则回写
"""
from __future__ import annotations

from .assemble import ContextBundle, assemble
from .config import DEFAULT_SETTINGS, Settings
from .conflict import ResolvedFact, resolve_candidates
from .extract.pipeline import ExtractorPipeline
from .feedback import FeedbackLoop
from .graph_app import GRAPH_BACKEND, MnemoRunner, RunResult
from .models import Assertion, Entity, Message
from .retrieve import parse_query, retrieve
from .store import Store

__all__ = [
    "Assertion",
    "ContextBundle",
    "DEFAULT_SETTINGS",
    "Entity",
    "ExtractorPipeline",
    "FeedbackLoop",
    "GRAPH_BACKEND",
    "Message",
    "MnemoRunner",
    "ResolvedFact",
    "RunResult",
    "Settings",
    "Store",
    "assemble",
    "parse_query",
    "resolve_candidates",
    "retrieve",
]

__version__ = "0.1.0"
