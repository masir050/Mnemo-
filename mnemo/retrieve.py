"""检索：查询解析 -> 候选召回 -> 三维打分 -> 子图扩展。"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .config import SOURCE_WEIGHTS, Settings, DEFAULT_SETTINGS
from .lexicons import PREDICATES, QUERY_INTENT_WORDS, halflife_of, importance_of
from .models import Assertion, Candidate, parse_iso
from .store import Store, bigrams


class Query:
    def __init__(self, text: str, entities: list[str], intents: list[str], terms: list[str]):
        self.text = text
        self.entities = entities
        self.intents = intents
        self.terms = terms

    def as_dict(self) -> dict:
        return {"text": self.text, "entities": self.entities, "intents": self.intents, "terms": len(self.terms)}


def parse_query(store: Store, text: str) -> Query:
    text = (text or "").strip()
    lowered = text.lower()

    # 实体名/别名最长匹配
    names: list[tuple[str, str]] = []
    for ent in store.all_entities():
        for n in ent.all_names():
            if len(n) >= 2 and n in text:
                names.append((n, ent.name))
    names.sort(key=lambda x: -len(x[0]))
    entities: list[str] = []
    for _, canonical in names:
        if canonical not in entities:
            entities.append(canonical)

    intents: list[str] = []
    for pid, words in QUERY_INTENT_WORDS.items():
        if any(w in lowered for w in words):
            intents.append(pid)

    terms = bigrams(text)
    return Query(text=text, entities=entities, intents=intents, terms=terms)


def _recency(assertion: Assertion, now: datetime, settings: Settings) -> float:
    anchor = parse_iso(assertion.valid_from) or parse_iso(assertion.created_at)
    if anchor is None:
        return 0.5
    age_days = max(0.0, (now - anchor).total_seconds() / 86400.0)
    halflife = halflife_of(assertion.predicate) or settings.recency_halflife_days
    return 0.5 ** (age_days / max(1.0, halflife))


def _importance(assertion: Assertion, settings: Settings) -> float:
    base = importance_of(assertion.predicate)
    src = SOURCE_WEIGHTS.get(assertion.source_kind, 0.5)
    status_w = {
        "active": settings.status_weight_active,
        "disputed": settings.status_weight_disputed,
        "superseded": settings.status_weight_superseded,
        "expired": settings.status_weight_expired,
    }.get(assertion.status, 0.5)
    boost = 1.08 if assertion.pinned else 1.0
    return min(1.0, base * src * status_w * boost)


def retrieve(
    store: Store,
    query: Query,
    settings: Settings | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> list[Candidate]:
    settings = settings or store.settings
    now = now or datetime.now(timezone.utc)
    limit = limit or settings.max_candidates

    hits = store.lookup_terms(query.terms)
    by_id: dict[str, list[tuple[str, float]]] = hits
    cands: dict[str, Candidate] = {}

    for aid, matched in by_id.items():
        a = store.get_assertion(aid)
        if not a:
            continue
        raw = sum(w for _, w in matched)
        rel = raw / (raw + 3.0)
        if a.entity_name in query.entities:
            rel = min(1.0, rel + 0.35)
        if a.predicate in query.intents:
            rel = min(1.0, rel + 0.30)
        cands[aid] = Candidate(
            assertion=a, relevance=rel, hop=0,
            matched_terms=[t for t, _ in matched],
        )

    # 一跳邻居：带出该实体的其他谓词
    for ent_name in query.entities:
        for row in store.conn.execute(
            "SELECT * FROM assertion WHERE entity_name = ? AND status IN ('active','disputed')", (ent_name,)
        ):
            a = Assertion.from_row(row)
            if a.id in cands:
                cands[a.id].relevance = min(1.0, cands[a.id].relevance + 0.1)
                continue
            rel = (0.42 + 0.2 * int(a.predicate in query.intents)) * settings.hop1_penalty / 0.55
            cands[a.id] = Candidate(assertion=a, relevance=min(1.0, rel), hop=1)

    out: list[Candidate] = []
    for c in cands.values():
        c.recency = _recency(c.assertion, now, settings)
        c.importance = _importance(c.assertion, settings)
        c.score = (
            settings.w_relevance * c.relevance
            + settings.w_recency * c.recency
            + settings.w_importance * c.importance
        )
        # 意图命中的谓词优先于同实体的其他字段
        if c.assertion.predicate in query.intents:
            c.score *= settings.intent_boost
        # 点名实体后，跨实体候选需命中 2 个以上查询词才放行
        if query.entities and c.assertion.entity_name not in query.entities:
            if len(c.matched_terms) < 2:
                continue
        if c.score >= settings.min_score:
            out.append(c)

    out.sort(key=lambda c: (-c.score, c.assertion.entity_name))
    return out[:limit]


def predicate_label(predicate: str) -> str:
    return PREDICATES.get(predicate, {}).get("label", predicate)


def recency_of(assertion: Assertion, settings: Settings) -> float:
    return _recency(assertion, datetime.now(timezone.utc), settings)


def saturation(x: float, k: float = 3.0) -> float:
    return x / (x + k)


def decay(age_days: float, halflife: float) -> float:
    return math.exp(-math.log(2) * age_days / max(1e-6, halflife))
