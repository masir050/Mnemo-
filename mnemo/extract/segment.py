"""轻量句法分析：分句 + 依存骨架。

没有引 HanLP/LTP：交接语料是短句、动宾结构为主，一套围绕「的/是/由/把」的
规则就能把主语、谓语、宾语三段切出来，足够给规则模板定位槽位；
而且零模型下载、可解释、改了立刻生效。需要更强句法时，`analyse()` 是唯一替换点。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")
CLAUSE_SPLIT = re.compile(r"[，,、]")

# 系动词 / 谓语标记
COPULAS = ("是", "为", "改成", "改为", "换成", "调整为", "变更为", "更新为", "已经", "由", "归")

SUBJECT_TAIL = ("的", "关于")


@dataclass
class Frame:
    """一句话的依存骨架（简化版：主语 - 谓语 - 宾语）。"""
    text: str
    subject: str = ""
    predicate: str = ""
    obj: str = ""
    markers: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.subject or self.obj)


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENT_SPLIT.split(text or "")]
    return [p for p in parts if p]


def split_clauses(sentence: str) -> list[str]:
    return [p.strip() for p in CLAUSE_SPLIT.split(sentence) if p.strip()]


_NP = r"[\u4e00-\u9fa5A-Za-z0-9_\-]{2,14}"


def analyse(sentence: str) -> Frame:
    """把一句话拆成 (主语, 谓语标记, 宾语)。

    依赖信号：
    - 「X的Y是Z」-> 定语结构，X 是主语，Y 是谓语，Z 是宾语
    - 「由X负责」-> 介词引导的施事，X 是宾语位的人
    - 「X改成Y」-> 主语 X，谓语 改成，宾语 Y
    """
    frame = Frame(text=sentence)
    s = sentence.strip()

    m = re.search(rf"(?P<subj>{_NP})的(?P<pred>[^\s，。]{{1,8}}?)(?P<cop>是|为|改成|改为|换成|调整为|变更为|更新为)(?P<obj>[^，。]{{1,40}})", s)
    if m:
        frame.subject, frame.predicate, frame.obj = m.group("subj"), m.group("pred"), m.group("obj").strip()
        frame.markers.append("attr-copula")
        return frame

    m = re.search(rf"(?P<subj>{_NP})(?P<cop>改成|改为|换成|调整为|变更为|更新为|现在是|目前是)(?P<obj>[^，。]{{1,40}})", s)
    if m:
        frame.subject, frame.predicate, frame.obj = m.group("subj"), m.group("cop"), m.group("obj").strip()
        frame.markers.append("svoc")
        return frame

    m = re.search(rf"由(?P<obj>{_NP})负责", s)
    if m:
        frame.obj = m.group("obj")
        frame.predicate = "负责"
        head = re.split(r"由", s)[0].strip(" 的，,")
        frame.subject = head[-14:] if head else ""
        frame.markers.append("by-agent")
        return frame

    for cop in COPULAS:
        if cop in s:
            left, _, right = s.partition(cop)
            frame.subject = left.strip()[-14:]
            frame.predicate = cop
            frame.obj = right.strip()
            frame.markers.append(f"copula:{cop}")
            return frame

    return frame


def extract_np(text: str) -> str:
    """从一个片段里剥出最可能的实体短语：去掉句末语气词和补充说明。"""
    text = re.split(r"[（(]", text)[0]
    text = re.sub(r"(了|的|呢|吧|啊|哦|哈|一下|那边|这边)$", "", text.strip())
    return text.strip()
