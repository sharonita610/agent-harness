import json

from harness.agent.loop import Agent
from harness.agent.types import LLMResponse, ToolCall
from harness.observability.tracer import TraceCollector, spans_from_events, write_jsonl
from harness.tools.permissions import auto_deny
from tests.test_loop import FakeProvider, make_registry, tc


def test_collector_builds_spans_from_live_run(tmp_path):
    p = FakeProvider([LLMResponse(tool_calls=[tc("add", a=1, b=2)]), LLMResponse(tool_calls=[tc("nope"), tc("write_note", text="x")]), LLMResponse(text="끝")])
    c = TraceCollector("run_test", "질문")
    agent = Agent(p, make_registry(), "sys", approval_handler=auto_deny, on_event=c.on_event)
    res = agent.run("질문")
    c.finish(res)
    kinds = [s.kind for s in c.spans]
    assert kinds.count("run") == 1 and kinds.count("llm") == 3 and kinds.count("tool") == 3 and kinds.count("approval") == 1
    by_name = {s.name: s for s in c.spans}
    assert by_name["tool.add"].status == "ok" and by_name["tool.add"].end is not None
    assert by_name["tool.nope"].status == "error"
    assert by_name["tool.write_note"].status == "not_approved" and by_name["human.approval.write_note"].status == "not_approved"
    assert c.root.status == "ok" and c.root.attrs["steps"] == 3 and c.root.attrs["stopped_reason"] == "final"
    assert all(s.end is not None for s in c.spans)
    summ = c.summary()
    assert summ["tool_errors"] == 1 and summ["llm_calls"] == 3
    path = write_jsonl(c, tmp_path / "t.jsonl")
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert lines[0]["type"] == "summary" and len(lines) == 1 + len(c.spans)


def test_spans_from_stored_events():
    events = [
        {"seq": 1, "kind": "step", "step": 1, "data": {}, "ts": "2026-10-28T10:00:00.000"},
        {"seq": 2, "kind": "llm", "step": 1, "data": {"latency_s": 1.5, "prompt_tokens": 100, "completion_tokens": 10, "tool_calls": [{}]}, "ts": "2026-10-28T10:00:01.500"},
        {"seq": 3, "kind": "tool_call", "step": 1, "data": {"name": "run_sql", "arguments": {"sql": "SELECT 1"}}, "ts": "2026-10-28T10:00:01.600"},
        {"seq": 4, "kind": "tool_result", "step": 1, "data": {"name": "run_sql", "chars": 50}, "ts": "2026-10-28T10:00:01.800"},
        {"seq": 5, "kind": "step", "step": 2, "data": {}, "ts": "2026-10-28T10:00:01.900"},
        {"seq": 6, "kind": "llm", "step": 2, "data": {"latency_s": 0.8, "text": "답"}, "ts": "2026-10-28T10:00:02.700"},
        {"seq": 7, "kind": "final", "step": 2, "data": {"answer": "답"}, "ts": "2026-10-28T10:00:02.700"},
    ]
    c = spans_from_events("run_x", "q", events, created_at="2026-10-28T09:59:59.000")
    d = {s.name: s.to_dict() for s in c.spans}
    assert d["tool.run_sql"]["duration_ms"] == 200.0 and d["llm.step1"]["duration_ms"] == 1500.0
    assert d["agent.run"]["duration_ms"] == 3700.0 and d["agent.run"]["status"] == "ok"
    assert d["llm.step1"]["attrs"]["prompt_tokens"] == 100


def test_stopped_run_marks_root_and_unfinished_tools():
    c = TraceCollector("run_y", "q")
    c.on_event({"kind": "tool_call", "step": 1, "data": {"name": "x", "arguments": {}}}, ts=10.0)
    c.on_event({"kind": "stopped", "step": 1, "data": {"reason": "cancelled"}}, ts=12.0)
    assert c.root.status == "cancelled" and c.spans[1].status == "unfinished" and c.spans[1].end == 12.0


def test_approval_wait_is_split_from_tool_execution():
    c = TraceCollector("run_z", "q")
    c.on_event({"kind": "tool_call", "step": 1, "data": {"name": "create_note", "arguments": {}}}, ts=100.0)
    c.on_event({"kind": "approval_request", "step": 1, "data": {"name": "create_note", "arguments": {}, "tier": "write"}}, ts=100.0)
    c.on_event({"kind": "approval_result", "step": 1, "data": {"name": "create_note", "approved": True}}, ts=120.0)
    c.on_event({"kind": "tool_result", "step": 1, "data": {"name": "create_note", "chars": 50}}, ts=120.5)
    c.on_event({"kind": "final", "step": 2, "data": {"answer": "ok"}}, ts=122.0)
    d = {s.name: s.to_dict() for s in c.spans}
    assert d["human.approval.create_note"]["duration_ms"] == 20000.0 and d["human.approval.create_note"]["parent_id"] == c.root.span_id
    assert d["tool.create_note"]["duration_ms"] == 500.0 and d["tool.create_note"]["attrs"]["waited_for_approval_s"] == 20.0
    summ = c.summary()
    assert summ["human_ms"] == 20000 and summ["tool_ms"] == 500 and summ["approvals"] == 1
