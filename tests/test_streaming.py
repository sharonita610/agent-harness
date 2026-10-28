"""스트리밍 조립: 텍스트 조각은 즉시 콜백, 도구 호출 조각은 인덱스별로 모아 하나의 호출로."""

from types import SimpleNamespace

from harness.agent.loop import Agent
from harness.agent.providers import assemble_stream
from harness.agent.types import LLMResponse, ToolCall
from tests.test_loop import make_registry


def chunk(content=None, tool_calls=None, finish=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=usage)


def test_assemble_text_stream_calls_on_delta_in_order():
    pieces = []
    r = assemble_stream(
        [
            chunk("지연 "),
            chunk("주문은 "),
            chunk("255건", finish="stop"),
            SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3)),
        ],
        pieces.append,
    )
    assert pieces == ["지연 ", "주문은 ", "255건"] and r.text == "지연 주문은 255건" and r.tool_calls == []
    assert r.finish_reason == "stop" and r.usage.prompt_tokens == 10 and r.usage.completion_tokens == 3


def test_assemble_tool_call_fragments():
    tcs = [
        chunk(
            tool_calls=[
                SimpleNamespace(
                    index=0, id="call_a", function=SimpleNamespace(name="run_sql", arguments='{"sql": "SEL')
                )
            ]
        ),
        chunk(
            tool_calls=[
                SimpleNamespace(index=0, id=None, function=SimpleNamespace(name=None, arguments='ECT 1", "limit": 1}'))
            ]
        ),
        chunk(
            tool_calls=[
                SimpleNamespace(index=1, id="call_b", function=SimpleNamespace(name="list_tables", arguments="{}"))
            ],
            finish="tool_calls",
        ),
    ]
    r = assemble_stream(tcs)
    assert [c.name for c in r.tool_calls] == ["run_sql", "list_tables"]
    assert (
        r.tool_calls[0].arguments == {"sql": "SELECT 1", "limit": 1}
        and r.tool_calls[0].id == "call_a"
        and r.tool_calls[0].parse_error is None
    )
    assert r.finish_reason == "tool_calls"


class StreamingFake:
    name = "fake-stream"
    model = "fake"
    supports_stream = True

    def __init__(self, script):
        self.script = list(script)

    def complete(self, messages, tools=None, on_delta=None):
        resp = self.script.pop(0)
        if on_delta and resp.text:
            for piece in resp.text.split("|"):
                on_delta(piece)
            resp = LLMResponse(
                text=resp.text.replace("|", ""),
                tool_calls=resp.tool_calls,
                usage=resp.usage,
                finish_reason=resp.finish_reason,
            )
        return resp


def test_loop_emits_delta_events_when_provider_streams():
    p = StreamingFake(
        [
            LLMResponse(tool_calls=[ToolCall(id="c1", name="add", arguments={"a": 1, "b": 2})]),
            LLMResponse(text="답은 |3|입니다"),
        ]
    )
    res = Agent(p, make_registry(), "sys").run("q")
    deltas = [e.data["text"] for e in res.events if e.kind == "delta"]
    assert deltas == ["답은 ", "3", "입니다"] and res.answer == "답은 3입니다"
    assert all(e.step == 2 for e in res.events if e.kind == "delta")
