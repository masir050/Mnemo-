"""词表与领域知识：谓词、触发词、重要度先验，抽取与检索共用。"""
from __future__ import annotations

import re

# predicate -> 中文名 / 别名 / 重要度先验 / 时间敏感半衰期（天）
PREDICATES: dict[str, dict] = {
    "owner": {
        "label": "负责人",
        "aliases": ["负责人", "责任人", "owner", "归口", "由谁负责", "负责"],
        "importance": 1.00,
        "halflife_days": 60,
    },
    "backup": {
        "label": "备份负责人",
        "aliases": ["备份", "backup", "代办", "代理人", "备岗"],
        "importance": 0.72,
        "halflife_days": 60,
    },
    "contact": {
        "label": "联系方式",
        "aliases": ["联系方式", "电话", "手机", "邮箱", "工位", "contact"],
        "importance": 0.60,
        "halflife_days": 240,
    },
    "status": {
        "label": "当前状态",
        "aliases": ["状态", "进展", "status", "目前", "现在"],
        "importance": 0.92,
        "halflife_days": 25,
    },
    "deadline": {
        "label": "截止时间",
        "aliases": ["截止", "deadline", "交付时间", "完成时间", "到期"],
        "importance": 0.95,
        "halflife_days": 30,
    },
    "schedule": {
        "label": "排期/值班",
        "aliases": ["排期", "值班", "班次", "轮值", "schedule"],
        "importance": 0.80,
        "halflife_days": 20,
    },
    "procedure": {
        "label": "操作流程",
        "aliases": ["流程", "步骤", "怎么操作", "procedure", "脚本", "命令"],
        "importance": 0.85,
        "halflife_days": 365,
    },
    "dependency": {
        "label": "依赖",
        "aliases": ["依赖", "前置", "卡在", "dependency", "依赖方"],
        "importance": 0.70,
        "halflife_days": 90,
    },
    "system": {
        "label": "所属系统",
        "aliases": ["系统", "环境", "地址", "集群", "system", "域名"],
        "importance": 0.78,
        "halflife_days": 180,
    },
    "risk": {
        "label": "风险",
        "aliases": ["风险", "隐患", "坑", "risk", "注意"],
        "importance": 0.82,
        "halflife_days": 120,
    },
    "handover_to": {
        "label": "交接对象",
        "aliases": ["交接给", "接手", "移交给", "handover"],
        "importance": 0.90,
        "halflife_days": 90,
    },
    "note": {
        "label": "备注",
        "aliases": ["备注", "补充", "note"],
        "importance": 0.40,
        "halflife_days": 180,
    },
}

# 查询侧的意图词 -> 谓词（用于给检索加权，不是硬过滤）
QUERY_INTENT_WORDS: dict[str, tuple[str, ...]] = {
    "owner": ("谁负责", "负责人", "谁管", "归谁", "找谁", "责任人", "负责人是谁"),
    "backup": ("备份", "备岗", "谁代"),
    "contact": ("电话", "联系方式", "邮箱", "怎么联系", "工位"),
    "status": ("状态", "进展", "进度", "现在怎么样", "到哪一步"),
    "deadline": ("什么时候", "截止", "到期", "deadline", "几号", "交付时间"),
    "schedule": ("排期", "值班", "几点的班", "班次"),
    "procedure": ("怎么操作", "流程", "步骤", "脚本", "命令", "怎么跑"),
    "dependency": ("依赖", "卡在", "前置"),
    "system": ("哪个系统", "环境", "地址", "集群"),
    "risk": ("风险", "注意什么", "坑"),
    "handover_to": ("交接给谁", "谁接手", "移交给"),
}

# 实体类型判定用后缀（粗粒度，命中即定；不中则 thing）
PERSON_HINTS = ("张", "李", "王", "刘", "陈", "杨", "赵", "周", "吴", "徐", "孙", "马", "朱", "胡", "郭", "何", "林", "高", "罗", "郑")

SYSTEM_SUFFIXES = ("系统", "平台", "集群", "服务", "网关", "库", "中台", "环境", "面板", "工单系统")

STOP_TOKENS = {
    "的", "了", "是", "在", "把", "被", "和", "与", "及", "或", "这", "那", "个",
    "我们", "你们", "他们", "一下", "一个", "现在", "目前", "还是", "然后", "所以",
    "已经", "需要", "可以", "应该", "今天", "明天", "昨天", "上午", "下午",
}

# 值归一化：常见中文日期/时间表达
CN_DATE_RE = re.compile(
    r"(?P<y>20\d{2})\s*[年\-/.]\s*(?P<m>\d{1,2})\s*[月\-/.]\s*(?P<d>\d{1,2})\s*[日号]?"
)
CN_DATE_SHORT_RE = re.compile(r"(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*[日号]")

# 一条消息里出现这些词，说明这是一次「变更」而不是「陈述」
CHANGE_MARKERS = ("改成", "改为", "换成", "调整为", "变更为", "更新为", "改由", "转给", "延期到", "推迟到", "提前到")

NEGATION_MARKERS = ("不再", "不是", "没有", "尚未", "未", "取消")


def predicate_of_label(label: str) -> str | None:
    """把任意中文说法映射回标准谓词。"""
    label = label.strip()
    for pid, meta in PREDICATES.items():
        if label == meta["label"]:
            return pid
        if label in meta["aliases"]:
            return pid
    return None


def importance_of(predicate: str) -> float:
    return PREDICATES.get(predicate, {}).get("importance", 0.5)


def halflife_of(predicate: str) -> float | None:
    return PREDICATES.get(predicate, {}).get("halflife_days")


def infer_entity_type(name: str) -> str:
    if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", name) and name[0] in PERSON_HINTS:
        return "person"
    if name.endswith(SYSTEM_SUFFIXES):
        return "system"
    if name.endswith(("项目", "专项", "工程", "计划")):
        return "project"
    if name.endswith(("流程", "规范", "手册", "任务", "作业", "脚本")):
        return "process"
    if name.endswith(("部", "组", "中心", "团队")):
        return "org"
    return "thing"
