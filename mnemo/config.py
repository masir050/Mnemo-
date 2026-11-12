"""全局配置，环境变量可覆盖。"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "mnemo.db"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """一次运行的有效配置。"""

    db_path: Path = DEFAULT_DB

    # --- 三维打分权重：相关度 / 时效 / 重要性 ---
    w_relevance: float = _env_float("MNEMO_W_REL", 0.5)
    w_recency: float = _env_float("MNEMO_W_REC", 0.28)
    w_importance: float = _env_float("MNEMO_W_IMP", 0.22)

    # --- 时效衰减：半衰期（天），recency = 0.5 ** (age_days / halflife) ---
    recency_halflife_days: float = _env_float("MNEMO_HALFLIFE", 45.0)

    # --- 状态权重：被取代/过期的旧值降权保留，而不是删除 ---
    status_weight_active: float = 1.0
    status_weight_disputed: float = 0.62
    status_weight_superseded: float = 0.30
    status_weight_expired: float = 0.22

    # --- 上下文预算 ---
    token_budget: int = _env_int("MNEMO_TOKEN_BUDGET", 900)
    min_score: float = _env_float("MNEMO_MIN_SCORE", 0.12)

    # --- 子图扩展：命中实体后是否带出一跳邻居 ---
    hop1_penalty: float = _env_float("MNEMO_HOP1_PENALTY", 0.55)
    max_candidates: int = _env_int("MNEMO_MAX_CANDIDATES", 200)

    # --- 意图加成：查询里出现「谁负责/什么时候到期」时，对应谓词的排序权重 ---
    intent_boost: float = _env_float("MNEMO_INTENT_BOOST", 1.35)

    # --- 抽取 ---
    # 低于该置信度的断言不落库（记入 rejected 日志，供规则迭代看）
    min_extract_confidence: float = _env_float("MNEMO_MIN_EXTRACT_CONF", 0.35)

    # --- 小模型兜底 ---
    # offline: 内置词表回退（无需网络）；openai: 走 OpenAI 兼容接口
    llm_provider: str = os.environ.get("MNEMO_LLM_PROVIDER", "offline")
    llm_model: str = os.environ.get("MNEMO_LLM_MODEL", "qwen2.5-1.5b-instruct")
    llm_base_url: str = os.environ.get("MNEMO_LLM_BASE_URL", "")

    def with_overrides(self, **kwargs) -> "Settings":
        return replace(self, **kwargs)


DEFAULT_SETTINGS = Settings()

# 来源权重：人工 > 工单 > 即时消息 > 周报 > 模型抽取
SOURCE_WEIGHTS: dict[str, float] = {
    "manual": 1.00,   # 面板人工确认
    "ticket": 0.90,   # 工单
    "im": 0.78,       # 即时消息
    "weekly": 0.66,   # 周报
    "llm": 0.50,      # 模型抽取，未经确认
}
