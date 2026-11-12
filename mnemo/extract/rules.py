"""规则模板层：正则槽位 + 谓词词表，主抽取通道。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..lexicons import CN_DATE_RE, CN_DATE_SHORT_RE, infer_entity_type

_PERSON = r"[\u4e00-\u9fa5]{2,4}"
# 非贪婪：贪婪会把「对账任务的操作」整段吃进去
_ENT = r"(?P<ent>[\u4e00-\u9fa5A-Za-z0-9_\-·]{2,16}?)"
_VALUE = r"(?P<val>[^，。；\n!?]{1,48})"
# 流程取值可能带逗号（「先跑 A，再跑 B」），只按句末标点断
_VALUE_LONG = r"(?P<val>[^。；\n!?]{1,80})"
_DATE = r"(?P<val>20\d{2}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*[日号]?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]|下?周[一二三四五六日天]|本周[一二三四五六日天]|今天|明天|后天)"


@dataclass
class Rule:
    id: str
    predicate: str
    pattern: re.Pattern
    confidence: float
    value_kind: str = "text"        # text / person / date / url
    entity_required: bool = True    # False 表示实体可从上下文继承
    note: str = ""
    origin: str = "builtin"
    fixed_value: str | None = None  # 记忆化规则用：命中即取该值，无视 val 分组
    fixed_entity: str | None = None # 纠正回写的规则通常把实体钉死


@dataclass
class RuleHit:
    entity: str
    predicate: str
    value: str
    confidence: float
    rule_id: str
    quote: str
    value_kind: str = "text"
    entity_from_context: bool = False
    note: str = ""
    is_change: bool = False


BUILTIN_RULES: list[Rule] = [
    Rule(
        "R.owner.attr", "owner",
        re.compile(rf"{_ENT}\s*的\s*(?:负责人|责任人|owner|归口|对接人)\s*(?:是|为|改成|改为|换成|调整为|：|:)\s*{_VALUE}"),
        0.90, "person",
    ),
    Rule(
        "R.owner.by", "owner",
        re.compile(rf"{_ENT}\s*(?:现在)?由\s*(?P<val>{_PERSON})\s*(?:来)?(?:负责|接手|承接)"),
        0.88, "person",
    ),
    Rule(
        "R.owner.assign", "handover_to",
        re.compile(rf"{_ENT}\s*(?:已经)?(?:交给|交接给|移交给|转给)\s*(?P<val>{_PERSON})"),
        0.86, "person",
    ),
    Rule(
        "R.backup", "backup",
        re.compile(rf"{_ENT}\s*的\s*(?:备份|备岗|代理人|backup)\s*(?:是|为|改成|：|:)\s*{_VALUE}"),
        0.86, "person",
    ),
    Rule(
        "R.contact", "contact",
        re.compile(rf"{_ENT}\s*的\s*(?:电话|手机|联系方式|邮箱|工位)\s*(?:是|为|：|:)\s*{_VALUE}"),
        0.84,
    ),
    Rule(
        "R.deadline.attr", "deadline",
        re.compile(rf"{_ENT}\s*的?\s*(?:截止时间|截止日期|截止|deadline|交付时间|完成时间|验收时间)\s*(?:是|为|改到|改成了|延期到|推迟到|提前到|定在|：|:)\s*{_DATE}"),
        0.90, "date",
    ),
    Rule(
        "R.deadline.bare", "deadline",
        re.compile(rf"(?:^|[，,、\s])(?:截止|deadline|交付时间)\s*(?:时间)?\s*(?:是|为|改到|延期到|推迟到|提前到|定在|：|:)\s*{_DATE}"),
        0.68, "date", entity_required=False, note="实体需从上下文继承",
    ),
    Rule(
        "R.status.attr", "status",
        re.compile(rf"{_ENT}\s*的?\s*(?:状态|进展|进度)\s*(?:是|为|：|:)\s*{_VALUE}"),
        0.88,
    ),
    Rule(
        "R.status.progress", "status",
        re.compile(rf"{_ENT}\s*(?:已经|已)\s*(?P<val>上线|完成|联调通过|验收通过|暂停|延期|卡住|回滚|下线)"),
        0.82,
    ),
    Rule(
        "R.schedule", "schedule",
        re.compile(rf"{_ENT}\s*的\s*(?:排期|值班|班次|轮值)\s*(?:是|为|改成|：|:)\s*{_VALUE}"),
        0.84,
    ),
    Rule(
        "R.procedure", "procedure",
        re.compile(rf"{_ENT}\s*的?\s*(?:操作流程|流程|操作步骤|脚本)\s*(?:是|为|：|:)\s*{_VALUE_LONG}"),
        0.82,
    ),
    Rule(
        "R.dependency", "dependency",
        re.compile(rf"{_ENT}\s*(?:依赖|卡在|前置是)\s*{_VALUE}"),
        0.76,
    ),
    Rule(
        "R.system", "system",
        re.compile(rf"{_ENT}\s*的?\s*(?:地址|环境|集群|域名)\s*(?:是|为|：|:)\s*{_VALUE}"),
        0.80,
    ),
    Rule(
        "R.risk", "risk",
        re.compile(rf"{_ENT}\s*(?:的风险是|的坑是|要注意)\s*{_VALUE}"),
        0.74,
    ),
]

# 变更类标记：命中说明这句话是「新值」，置信度可以给高一点
CHANGE_PAT = re.compile(r"改成|改为|换成|调整为|变更为|更新为|延期到|推迟到|提前到|转给|改由")

# 指代词不能当实体
BAD_ENTITIES = {"这个", "那个", "咱们", "我们", "你们", "他们", "现在", "目前", "这边", "那边", "事情", "情况", "问题"}

DATE_PATTERNS = (CN_DATE_RE, CN_DATE_SHORT_RE)


def normalize_date(raw: str, default_year: int | None = None) -> str | None:
    raw = raw.strip()
    m = CN_DATE_RE.search(raw)
    if m:
        return f"{int(m.group('y')):04d}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"
    m = CN_DATE_SHORT_RE.search(raw)
    if m and default_year:
        return f"{default_year:04d}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    return None


def clean_value(value: str, kind: str) -> str:
    v = value.strip().strip("，,。；;：:、 ")
    v = re.sub(r"(了|的呢|呢|吧|啊|哦|哈|啦)$", "", v).strip()
    if kind == "person":
        # 「李强那边的人」要落成「李强」：人名后面常跟方位/指代尾巴，先剥再截。
        v = re.sub(r"(那边|这边|那头|那头儿|的人|他们|她们|同事)+$", "", v).strip()
        m = re.match(rf"({_PERSON})", v)
        if m:
            v = m.group(1)
    if kind == "text":
        v = re.sub(r"(那边|这边|这样子|这样)$", "", v).strip()
    return v

def clean_entity(entity: str) -> str:
    """剥掉正则吃进来的多余成分：左边界按「的/和/说」截，右边界剥口语尾巴。"""
    e = entity.strip().strip("，,。；;：:、 的")
    e = re.sub(r"^(那么|然后|所以|但是|另外|还有|以及)", "", e)
    e = re.sub(r"^(负责人|责任人|对接人|联系人|备份|备岗)", "", e)
    # 「张伟说支付网关的负责人」-> 说 之后才是实体；并列取最后一个（真正的主语）。
    e = re.split(r"(说|问|提醒|补充|建议|强调|反馈)", e)[-1]
    e = re.split(r"[和与及、]", e)[-1]
    e = e.split("的")[0]              # 中心词在「的」左边：「数据治理专项的负责人」-> 数据治理专项
    e = re.sub(r"(?:(?:这块儿|这块|这摊|那边|这边|现在|目前))+$", "", e)
    return e.strip()


def is_plausible_entity(entity: str) -> bool:
    if not entity or len(entity) < 2:
        return False
    if entity in BAD_ENTITIES:
        return False
    if entity.isdigit():
        return False
    if infer_entity_type(entity) == "thing" and len(entity) < 2:
        return False
    return True


def match_rules(text: str, context_entity: str | None = None, extra_rules: list[Rule] | None = None) -> list[RuleHit]:
    """对一句话跑全部规则。同一 (predicate, value) 只保留置信度最高的一条。"""
    hits: dict[tuple[str, str], RuleHit] = {}
    rules = list(BUILTIN_RULES) + list(extra_rules or [])
    changed = bool(CHANGE_PAT.search(text))

    for rule in rules:
        for m in rule.pattern.finditer(text):
            gd = m.groupdict()
            entity = clean_entity(gd.get("ent") or "")
            raw_val = gd.get("val") or ""
            from_ctx = False

            if rule.fixed_entity:
                entity = rule.fixed_entity

            if not entity:
                if rule.entity_required:
                    continue
                if not context_entity:
                    continue
                entity = context_entity
                from_ctx = True

            if not is_plausible_entity(entity):
                continue

            if rule.fixed_value:
                value = rule.fixed_value
            else:
                value = clean_value(raw_val, rule.value_kind)
            if rule.value_kind == "date" and not rule.fixed_value:
                normalized = normalize_date(value, default_year=2026)
                if not normalized:
                    continue
                value = normalized

            if not value:
                continue

            conf = rule.confidence
            if changed:
                # 显式变更表述是「新值」的强信号，给它一个台阶，好压过旧值的惯性
                conf = min(0.96, conf + 0.05)
            if from_ctx:
                conf -= 0.16
            if len(value) > 30:
                conf -= 0.05

            hit = RuleHit(
                entity=entity, predicate=rule.predicate, value=value,
                confidence=round(conf, 3), rule_id=rule.id, quote=m.group(0).strip(),
                value_kind=rule.value_kind, entity_from_context=from_ctx,
                note=rule.note, is_change=changed,
            )
            key = (hit.predicate, hit.value)
            if key not in hits or hits[key].confidence < hit.confidence:
                hits[key] = hit

    return list(hits.values())


def compile_learned_rule(pattern: str, predicate: str, confidence: float, rule_id: str,
                         origin: str = "", template: dict | None = None) -> Rule | None:
    """把面板里学到的模式编译成 Rule。编译失败说明用户输入的正则不可用，直接丢弃。"""
    try:
        compiled = re.compile(pattern)
    except re.error:
        return None
    template = template or {}
    return Rule(
        id=rule_id, predicate=predicate, pattern=compiled,
        confidence=confidence, note=origin or "learned", origin="learned",
        value_kind=template.get("value_kind", "text"),
        fixed_value=template.get("fixed_value"),
        fixed_entity=template.get("fixed_entity"),
        entity_required=template.get("entity_required", True),
    )
