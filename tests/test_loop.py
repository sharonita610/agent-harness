"""에이전트 루프 단위 테스트: 가짜 프로바이더로 스크립트된 응답을 흘려 넣는다."""
from pydantic import BaseModel, Field

from harness.agent.loop import Agent
from harness.agent.types import LLMResponse, ToolCall
from harness.tools.permissions import auto_approve, auto_deny
from harness.tools.registry import Tier, Tool, ToolRegistry


class AddArgs(BaseModel):
    a: int
    b: int


class NoteArgs(BaseModel):
    text: str = Field(min_length=1)


calls_made: list[str] = []


def make_registry():
    calls_made.clear()
    return ToolRegistry([
        Tool("add", "더하기", AddArgs, lambda x: {"sum": x.a + x.b}),
        Tool("write_note", "메모", NoteArgs, lambda x: calls_made.append(x.text) or {"ok": True}, Tier.WRITE),
        Tool("nuke", "삭제", NoteArgs, lambda x: calls_made.append("NUKE") or {"ok": True}, Tier.DESTRUCTIVE),
    ])


class FakeProvider:
    name = "fake"
    model = "fake"

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def complete(self, messages, tools=None):
        self.seen.append(list(messages))  # 스냅샷 (Agent 가 같은 리스트를 계속 수정함)
        return self.script.pop(0) if self.script else LLMResponse(text="끝")


def tc(name, **args):
    return ToolCall(id=f"c{len(args)}_{name}", name=name, arguments=args)


def test_tool_then_final():
    p = FakeProvider([LLMResponse(tool_calls=[tc("add", a=2, b=3)]), LLMResponse(text="답은 5")])
    res = Agent(p, make_registry(), "sys").run("2+3?")
    assert res.answer == "답은 5" and res.steps == 2 and res.stopped_reason == "final"
    assert res.tool_calls[0]["status"] == "ok" and '"sum": 5' in res.tool_calls[0]["result_preview"]
    # 두 번째 호출 때 tool 결과가 메시지에 들어가 있어야 한다
    assert any(m["role"] == "tool" and '"sum": 5' in m["content"] for m in p.seen[1])


def test_validation_error_is_fed_back_and_counted():
    p = FakeProvider([LLMResponse(tool_calls=[tc("add", a="x", b=1)]), LLMResponse(tool_calls=[tc("add", a=1, b=1)]), LLMResponse(text="2")])
    res = Agent(p, make_registry(), "sys").run("q")
    assert res.tool_calls[0]["status"] == "validation_error"
    assert "인자 검증 실패" in p.seen[1][-1]["content"]
    assert res.answer == "2"


def test_write_requires_approval_and_deny_blocks_execution():
    p = FakeProvider([LLMResponse(tool_calls=[tc("write_note", text="hi")]), LLMResponse(text="승인 필요")])
    res = Agent(p, make_registry(), "sys", approval_handler=auto_deny).run("메모 남겨")
    assert res.approvals == [{"step": 1, "name": "write_note", "arguments": {"text": "hi"}, "approved": False}]
    assert calls_made == [] and res.tool_calls[0]["status"] == "not_approved"


def test_write_runs_when_approved():
    p = FakeProvider([LLMResponse(tool_calls=[tc("write_note", text="hi")]), LLMResponse(text="완료")])
    res = Agent(p, make_registry(), "sys", approval_handler=auto_approve).run("메모 남겨")
    assert calls_made == ["hi"] and res.approvals[0]["approved"] is True


def test_destructive_is_denied_even_with_auto_approve():
    p = FakeProvider([LLMResponse(tool_calls=[tc("nuke", text="x")]), LLMResponse(text="불가")])
    res = Agent(p, make_registry(), "sys", approval_handler=auto_approve).run("지워")
    assert res.tool_calls[0]["status"] == "denied" and "NUKE" not in calls_made


def test_salvages_tool_call_from_text():
    p = FakeProvider([LLMResponse(text='<tool_call>{"name":"add","arguments":{"a":1,"b":1}}</tool_call>'), LLMResponse(text="2")])
    res = Agent(p, make_registry(), "sys").run("q")
    assert res.tool_calls[0]["name"] == "add" and res.tool_calls[0]["status"] == "ok"


def test_too_many_tool_errors_stops():
    p = FakeProvider([LLMResponse(tool_calls=[tc("nope")]) for _ in range(10)])
    res = Agent(p, make_registry(), "sys", max_tool_errors=2).run("q")
    assert res.stopped_reason == "too_many_tool_errors" and res.steps == 3


def test_max_steps_triggers_wrap_up_without_tools():
    p = FakeProvider([LLMResponse(tool_calls=[tc("add", a=1, b=1)]) for _ in range(3)] + [LLMResponse(text="정리")])
    res = Agent(p, make_registry(), "sys", max_steps=3).run("q")
    assert res.stopped_reason == "max_steps" and res.answer == "정리"
    assert p.seen[-1][-1]["role"] == "user" and "도구는 더 쓰지" in p.seen[-1][-1]["content"]
