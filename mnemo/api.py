"""HTTP 接口 + SSE 流式回传。"""
from __future__ import annotations

# 允许直接运行本文件：补上项目根并按包导入（推荐入口是 main.py）。
if __name__ == "__main__" and not __package__:
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    __package__ = "mnemo"

import json
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import DEFAULT_SETTINGS
from .agent import AgentRunner, ModelError, build_model
from .db import DEFAULT_DB
from .extract.pipeline import ExtractorPipeline
from .feedback import FeedbackLoop
from .graph_app import GRAPH_BACKEND, MnemoRunner
from .models import Message, now_iso
from .retrieve import parse_query, retrieve
from .store import Store
from .tokens import memory_tokens

WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="Mnemo", version="0.1.0")

_store: Store | None = None
_runner: MnemoRunner | None = None
_feedback: FeedbackLoop | None = None
_pipeline: ExtractorPipeline | None = None
_agent: AgentRunner | None = None


def get_store() -> Store:
    global _store, _runner, _feedback
    if _store is None:
        db_path = os.environ.get("MNEMO_DB", str(DEFAULT_DB))
        _store = Store(db_path, DEFAULT_SETTINGS)
        _runner = MnemoRunner(_store)
        _feedback = FeedbackLoop(_store)
    return _store


def get_pipeline() -> ExtractorPipeline:
    global _pipeline
    get_store()
    if _pipeline is None:
        assert _store is not None
        _pipeline = ExtractorPipeline(_store)
    return _pipeline


def get_runner() -> MnemoRunner:
    get_store()
    assert _runner is not None
    return _runner


def get_feedback() -> FeedbackLoop:
    get_store()
    assert _feedback is not None
    return _feedback


def get_agent() -> AgentRunner:
    """agent 需要模型；没配就 503，而不是悄悄退回固定流水线。"""
    global _agent
    store = get_store()
    if _agent is None:
        try:
            model = build_model(store.settings)
        except ModelError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        _agent = AgentRunner(store, model)
    return _agent


# --------------------------------------------------------------------------- 模型
class CorrectIn(BaseModel):
    assertion_id: str
    action: str = Field(pattern="^(correct|reject|confirm)$")
    new_value: str = ""
    operator: str = "panel"


class RuleIn(BaseModel):
    pattern: str
    predicate: str
    operator: str = "panel"


class ToggleIn(BaseModel):
    active: bool


class IngestIn(BaseModel):
    """灌一条原始消息并抽取。"""
    text: str
    speaker: str = "panel"
    channel: str = "im"
    ts: str = ""
    thread: str = ""
    id: str = ""


# --------------------------------------------------------------------------- 基础
@app.get("/api/health")
def health():
    store = get_store()
    return {"ok": True, "backend": GRAPH_BACKEND, "db": str(store.conn.execute("PRAGMA database_list").fetchone()[2])}


@app.get("/api/stats")
def stats():
    store = get_store()
    data = store.stats()
    data["backend"] = GRAPH_BACKEND
    data["token_budget"] = DEFAULT_SETTINGS.token_budget
    return data


@app.get("/api/entities")
def entities():
    store = get_store()
    out = []
    for ent in store.all_entities():
        rows = store.conn.execute(
            "SELECT predicate, value, confidence, status FROM assertion "
            "WHERE entity_id = ? AND status IN ('active','disputed') ORDER BY predicate",
            (ent.id,),
        ).fetchall()
        out.append(
            {
                "id": ent.id,
                "name": ent.name,
                "type": ent.type,
                "aliases": ent.aliases,
                "facts": [
                    {"predicate": r["predicate"], "value": r["value"],
                     "confidence": round(r["confidence"], 2), "status": r["status"]}
                    for r in rows
                ],
            }
        )
    return out


@app.get("/api/assertions")
def assertions(
    entity: str | None = None,
    status: str | None = None,
    predicate: str | None = None,
    limit: int = Query(200, le=1000),
):
    store = get_store()
    sql = "SELECT * FROM assertion WHERE 1=1"
    args: list = []
    if entity:
        sql += " AND entity_name = ?"
        args.append(entity)
    if status:
        sql += " AND status = ?"
        args.append(status)
    if predicate:
        sql += " AND predicate = ?"
        args.append(predicate)
    sql += " ORDER BY entity_name, predicate, COALESCE(valid_from, created_at) DESC LIMIT ?"
    args.append(limit)
    rows = store.conn.execute(sql, tuple(args)).fetchall()
    return [
        {
            "id": r["id"],
            "entity": r["entity_name"],
            "predicate": r["predicate"],
            "value": r["value"],
            "status": r["status"],
            "confidence": round(r["confidence"], 3),
            "valid_from": r["valid_from"],
            "valid_to": r["valid_to"],
            "source_kind": r["source_kind"],
            "source_message_id": r["source_message_id"],
            "source_quote": r["source_quote"],
            "extractor": r["extractor"],
            "pinned": bool(r["pinned"]),
            "is_change": bool(r["is_change"]),
            "note": r["note"],
            "tokens": round(memory_tokens(store.get_assertion(r["id"])), 1),
        }
        for r in rows
    ]


@app.get("/api/chain/{assertion_id}")
def chain(assertion_id: str):
    store = get_store()
    a = store.get_assertion(assertion_id)
    if a is None:
        raise HTTPException(404, "assertion not found")
    return {
        "slot": a.slot,
        "versions": [
            {
                "id": x.id, "value": x.value, "status": x.status,
                "confidence": round(x.confidence, 3),
                "valid_from": x.valid_from, "valid_to": x.valid_to,
                "source": f"{x.source_kind}:{x.source_message_id}",
                "extractor": x.extractor, "pinned": x.pinned, "note": x.note,
            }
            for x in store.chain(a.slot)
        ],
    }


@app.get("/api/messages/{message_id}")
def message(message_id: str):
    store = get_store()
    m = store.get_message(message_id)
    if m is None:
        raise HTTPException(404, "message not found")
    return {
        "id": m.id, "channel": m.channel, "speaker": m.speaker,
        "ts": m.ts, "text": m.text, "thread": m.thread,
        "assertions": [
            {"id": a.id, "predicate": a.predicate, "value": a.value, "status": a.status}
            for a in store.message_assertions(m.id)
        ],
    }


# --------------------------------------------------------------------------- 检索/问答
@app.get("/api/ask")
def ask(q: str):
    return get_runner().run(q).as_dict()


@app.get("/api/ask/stream")
def ask_stream(q: str):
    """SSE：每个节点一组事件，最后一条是 final。"""

    def gen():
        try:
            for ev in get_runner().stream(q):
                yield f"data: {ev.to_json()}\n\n"
        except Exception as exc:  # 前端要能看到失败原因，不能静默断流
            payload = json.dumps({"stage": "error", "message": str(exc)}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class AgentAsk(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@app.get("/api/agent/info")
def agent_info():
    store = get_store()
    s = store.settings
    from .agent.tools import SCHEMA_NAMES

    return {
        "ready": bool(s.llm_base_url and s.llm_api_key),
        "model": s.llm_model,
        "base_url": s.llm_base_url,
        "max_steps": s.agent_max_steps,
        "max_tool_calls": s.agent_max_tool_calls,
        "tools": sorted(SCHEMA_NAMES),
    }


@app.post("/api/agent")
def agent_ask(body: AgentAsk):
    return get_agent().run(body.question).to_dict()


@app.get("/api/agent/stream")
def agent_stream(q: str):
    """SSE：每步推一个事件（thought / tool_call / observation），最后 final。"""

    def gen():
        import queue as _q
        import threading

        box: _q.Queue = _q.Queue()
        out: dict = {}

        def worker():
            try:
                out["result"] = get_agent().run(q, on_event=lambda s: box.put(s))
            except Exception as exc:
                out["error"] = str(exc)
            finally:
                box.put(None)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            step = box.get()
            if step is None:
                break
            payload = {"stage": "agent_step", "step": step.to_dict()}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        if "result" in out:
            payload = {"stage": "final", **out["result"].to_dict()}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        elif "error" in out:
            payload = {"stage": "error", "message": out["error"]}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/trace")
def trace(q: str, limit: int = 8):
    """只做检索，不生成回答。"""
    store = get_store()
    parsed = parse_query(store, q)
    cands = retrieve(store, parsed)
    return {
        "parsed": parsed.as_dict(),
        "candidates": [
            {
                "id": c.assertion.id, "entity": c.assertion.entity_name,
                "predicate": c.assertion.predicate, "value": c.assertion.value,
                "matched_terms": c.matched_terms[:8], "hop": c.hop,
                **c.breakdown,
            }
            for c in cands[: limit * 4]
        ],
    }


# --------------------------------------------------------------------------- 纠错
@app.post("/api/ingest")
def ingest(body: IngestIn):
    if not body.text.strip():
        raise HTTPException(400, "text 不能为空")
    msg = Message(
        id=body.id or f"m_{uuid.uuid4().hex[:10]}",
        channel=body.channel, speaker=body.speaker,
        ts=body.ts or now_iso(), text=body.text.strip(), thread=body.thread,
    )
    res = get_pipeline().ingest(msg)
    return res.as_dict()


@app.post("/api/corrections")
def correct(body: CorrectIn):
    fb = get_feedback()
    try:
        if body.action == "correct":
            out = fb.correct(body.assertion_id, body.new_value, body.operator)
        elif body.action == "reject":
            out = fb.reject(body.assertion_id, body.new_value, body.operator)
        else:
            out = fb.confirm(body.assertion_id, body.operator)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # 让抽取器立即加载新规则
    get_pipeline().refresh_rules()
    return out.as_dict()


@app.get("/api/rules")
def rules():
    return get_feedback().list_rules()


@app.post("/api/rules")
def add_rule(body: RuleIn):
    fb = get_feedback()
    try:
        rid = fb.add_pattern_rule(body.pattern, body.predicate, body.operator)
    except Exception as exc:
        raise HTTPException(400, f"正则不可用：{exc}")
    get_pipeline().refresh_rules()
    return {"id": rid}


@app.post("/api/rules/{rule_id}/toggle")
def toggle_rule(rule_id: str, body: ToggleIn):
    get_feedback().toggle_rule(rule_id, body.active)
    get_pipeline().refresh_rules()
    return {"ok": True}


@app.get("/api/rejected")
def rejected(limit: int = 50):
    store = get_store()
    rows = store.conn.execute(
        "SELECT * FROM rejected ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- 静态
@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


if __name__ == "__main__":
    # 直接运行本文件时的兜底
    import sys

    from .serve import serve

    _argv = sys.argv[1:]
    _port = int(_argv[_argv.index("--port") + 1]) if "--port" in _argv else None
    raise SystemExit(serve(port=_port, open_browser="--no-browser" not in _argv))
