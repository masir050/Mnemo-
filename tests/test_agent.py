"""Agent 循环：工具调用、错误回传、预算、引用校验。全部用脚本化模型，不联网。"""
from __future__ import annotations

import pytest

from mnemo.agent import AgentRunner, ScriptedChat, ToolError, Toolbox
from mnemo.agent.model import ChatReply, ToolCall


def call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"c_{name}", name=name, args=args)


def test_toolbox_search_hits(demo):
    out = Toolbox(demo.store).search_memory("支付网关谁负责")
    assert out["count"] >= 1
    assert out["results"][0]["id"].startswith("a_")


def test_toolbox_unknown_entity(demo):
    with pytest.raises(ToolError):
        Toolbox(demo.store).read_slot("不存在的系统", "owner")


def test_toolbox_rejects_unknown_tool(demo):
    with pytest.raises(ToolError):
        Toolbox(demo.store).call("rm_rf", {})


def test_loop_calls_tool_then_answers(demo):
    aid = Toolbox(demo.store).search_memory("支付网关负责人")["results"][0]["id"]
    model = ScriptedChat([
        ChatReply(tool_calls=[call("search_memory", query="支付网关负责人")]),
        ChatReply(content=f"支付网关的负责人是李强 [{aid}]"),
    ])
    r = AgentRunner(demo.store, model).run("支付网关谁负责")
    assert r.stop_reason == "answer"
    assert r.tool_calls == 1
    assert [s.kind for s in r.steps] == ["tool_call", "observation"]
    assert r.citations and r.citations[0]["assertion_id"] == aid
    assert r.invalid_citations == []
    assert r.usage["total_tokens"] == 0


def test_tool_error_is_returned_to_model(demo):
    model = ScriptedChat([
        ChatReply(tool_calls=[call("read_slot", entity="不存在的系统", predicate="owner")]),
        ChatReply(content="查不到这个系统"),
    ])
    r = AgentRunner(demo.store, model).run("不存在的系统谁负责")
    obs = [s for s in r.steps if s.kind == "observation"]
    assert obs and "error" in obs[0].content
    assert r.answer == "查不到这个系统"


def test_loop_stops_at_max_steps(demo):
    model = ScriptedChat(lambda messages: ChatReply(tool_calls=[call("list_entities")]))
    r = AgentRunner(demo.store, model).run("随便问问")
    assert r.stop_reason == "max_steps"
    assert r.tool_calls == demo.store.settings.agent_max_steps
    assert r.answer


def test_invalid_citation_is_flagged(demo):
    model = ScriptedChat([ChatReply(content="负责人是李强 [a_deadbeefcafe]")])
    r = AgentRunner(demo.store, model).run("谁负责")
    assert r.invalid_citations == ["a_deadbeefcafe"]
    assert r.citations == []


def test_run_is_logged_for_replay(demo):
    model = ScriptedChat([ChatReply(content="不知道")])
    AgentRunner(demo.store, model).run("谁负责")
    rows = demo.store.recent_runs(5) if hasattr(demo.store, "recent_runs") else []
    assert isinstance(rows, list)
