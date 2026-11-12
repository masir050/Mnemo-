"""小模型兜底通道：只在规则不命中时调用，输出置信度上限 0.55。

    offline  内置词表 + 句法骨架，零依赖
    openai   OpenAI 兼容接口，配了 base_url 就用
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from ..config import Settings
from ..lexicons import PREDICATES
from .segment import analyse


@dataclass
class ModelHit:
    entity: str
    predicate: str
    value: str
    confidence: float
    extractor: str


class BaseExtractor:
    name = "base"

    def available(self) -> bool:
        return True

    def extract(self, text: str, context_entity: str | None = None) -> list[ModelHit]:
        raise NotImplementedError


class OfflineExtractor(BaseExtractor):
    """离线兜底：句法骨架 + 谓词同义词，抓规则未覆盖但结构清楚的句子。"""

    name = "offline"

    GENERIC = [
        (re.compile(r"(?P<ent>[\u4e00-\u9fa5A-Za-z0-9_\-]{2,14})\s*归\s*(?P<val>[\u4e00-\u9fa5]{2,8})\s*(?:管|负责)"), "owner", 0.52),
        (re.compile(r"(?P<ent>[\u4e00-\u9fa5A-Za-z0-9_\-]{2,14})\s*这块(?:儿)?\s*(?:现在)?(?:是|由)\s*(?P<val>[\u4e00-\u9fa5]{2,8})\s*(?:在)?(?:管|负责|跟)"), "owner", 0.48),
        (re.compile(r"(?P<ent>[\u4e00-\u9fa5A-Za-z0-9_\-]{2,14})\s*卡在\s*(?P<val>[^，。；]{2,20})"), "dependency", 0.50),
        (re.compile(r"(?P<ent>[\u4e00-\u9fa5A-Za-z0-9_\-]{2,14})\s*(?:还没|尚未|没有)\s*(?P<val>上线|验收|联调|启动|交付)"), "status", 0.50),
        (re.compile(r"(?P<ent>[\u4e00-\u9fa5A-Za-z0-9_\-]{2,14})\s*(?:这块儿?|这摊)\s*(?:得|要)\s*(?:先)?\s*(?P<val>[^，。；]{2,24})"), "procedure", 0.40),
    ]

    PREDICATE_WORDS = {
        "owner": ("负责", "归口", "管"),
        "backup": ("备份", "备岗", "代"),
        "status": ("状态", "进展", "进度"),
        "deadline": ("截止", "到期", "deadline"),
        "schedule": ("排期", "值班", "班次"),
        "procedure": ("流程", "步骤", "脚本"),
        "risk": ("风险", "隐患", "坑"),
        "system": ("系统", "环境", "集群"),
    }

    def extract(self, text: str, context_entity: str | None = None) -> list[ModelHit]:
        hits: list[ModelHit] = []
        for pattern, predicate, conf in self.GENERIC:
            for m in pattern.finditer(text):
                ent = m.group("ent").strip(" 的，,")
                val = m.group("val").strip(" 的，,。")
                if len(ent) < 2 or len(val) < 1:
                    continue
                hits.append(ModelHit(ent, predicate, val, conf, f"llm:{self.name}"))

        # 句法骨架通道：主谓宾齐全但规则未覆盖时，用谓词词表回推
        frame = analyse(text)
        if frame.subject and frame.obj and len(frame.obj) <= 32:
            for predicate, words in self.PREDICATE_WORDS.items():
                if any(w in frame.text for w in words):
                    hits.append(
                        ModelHit(
                            frame.subject.strip(" 的，,"),
                            predicate,
                            frame.obj.strip(" 的，,。"),
                            0.44,
                            f"llm:{self.name}",
                        )
                    )
                    break
        return _dedupe(hits)


class OpenAICompatExtractor(BaseExtractor):
    """OpenAI 兼容接口。仅当 MNEMO_LLM_PROVIDER=openai 且配置了 base_url 时启用。"""

    name = "openai"

    PROMPT = (
        "你是信息抽取器。从给定的交接消息里抽取 (主体实体, 谓词, 取值) 三元组。\n"
        "谓词只能取以下之一：" + "/".join(PREDICATES.keys()) + "\n"
        "只输出 JSON 数组，每项形如 {\"entity\":..,\"predicate\":..,\"value\":..}。\n"
        "抽不到就输出 []。不要解释。\n消息："
    )

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None

    def available(self) -> bool:
        return bool(self.settings.llm_base_url and os.environ.get("MNEMO_LLM_API_KEY"))

    def _ensure(self):
        if self._client is not None:
            return self._client
        import httpx  # 延迟导入，没装也不影响离线模式

        self._client = httpx.Client(
            base_url=self.settings.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {os.environ.get('MNEMO_LLM_API_KEY','')}"},
            timeout=20.0,
        )
        return self._client

    def extract(self, text: str, context_entity: str | None = None) -> list[ModelHit]:
        if not self.available():
            return []
        client = self._ensure()
        try:
            resp = client.post(
                "/chat/completions",
                json={
                    "model": self.settings.llm_model,
                    "temperature": 0.0,
                    "messages": [{"role": "user", "content": self.PROMPT + text}],
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(_strip_fence(content))
        except Exception:
            return []
        out: list[ModelHit] = []
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            ent, pred, val = item.get("entity"), item.get("predicate"), item.get("value")
            if not (ent and pred in PREDICATES and val):
                continue
            out.append(ModelHit(str(ent), pred, str(val), 0.55, f"llm:{self.name}"))
        return _dedupe(out)


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()


def _dedupe(hits: list[ModelHit]) -> list[ModelHit]:
    best: dict[tuple[str, str, str], ModelHit] = {}
    for h in hits:
        key = (h.entity, h.predicate, h.value)
        if key not in best or best[key].confidence < h.confidence:
            best[key] = h
    return list(best.values())


def build_extractor(settings: Settings) -> BaseExtractor:
    if settings.llm_provider == "openai":
        ext = OpenAICompatExtractor(settings)
        if ext.available():
            return ext
    return OfflineExtractor()
