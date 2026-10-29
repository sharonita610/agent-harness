"""모델 라우팅(ADR-024): 싼 모델로 돌리다가 막히면 강한 모델로 올리는 프로바이더."""

from __future__ import annotations

import pytest

from harness.agent.loop import Agent
from harness.agent.providers import RoutingProvider, make_provider
from harness.agent.types import LLMResponse, ToolCall, Usage
from tests.test_loop import FakeProvider, make_registry, tool_call


def _resp(text=None, calls=(), finish="stop", p=10, c=5, parse_error=None):
    tcs = [ToolCall(id="c1", name=n, arguments=a, parse_error=parse_error) for n, a in calls]
    return LLMResponse(
        text=text, tool_calls=tcs, usage=Usage(prompt_tokens=p, completion_tokens=c), finish_reason=finish
    )


def _router(cheap_script, strong_script, **kw):
    cheap, strong = FakeProvider(cheap_script), FakeProvider(strong_script)
    return RoutingProvider(cheap, strong, "local", "openai:gpt-5-mini", **kw), cheap, strong


def _msgs(*tail):
    return [{"role": "system", "content": "sys"}, {"role": "user", "content": "질문"}, *tail]


def test_good_cheap_response_stays_cheap():
    r, cheap, strong = _router([_resp(text="답")], [])
    out = r.complete(_msgs(), None, step=1)
    assert out.text == "답" and out.model == "local" and out.route is None
    assert out.usage.by_model == {"local": {"prompt_tokens": 10, "completion_tokens": 5, "calls": 1}}
    assert strong.seen == []


@pytest.mark.parametrize(
    "cheap_resp, why",
    [
        (_resp(calls=[("add", {})], parse_error="not json"), "parse_error"),
        (_resp(text="", finish="length"), "truncated"),
        (_resp(text="   "), "empty"),
    ],
)
def test_weak_cheap_response_is_retried_on_strong_with_both_usages(cheap_resp, why):
    r, cheap, strong = _router([cheap_resp], [_resp(text="강한 답", p=100, c=20)])
    out = r.complete(_msgs(), None, step=2)
    assert out.text == "강한 답" and out.model == "openai:gpt-5-mini" and out.route == f"cheap_response:{why}"
    assert out.usage.prompt_tokens == 110 and out.usage.by_model["openai:gpt-5-mini"]["calls"] == 1
    assert out.usage.by_model["local"]["calls"] == 1 and strong.seen[0] == cheap.seen[0]


def test_trailing_tool_errors_escalate_before_calling_cheap():
    r, cheap, strong = _router([_resp(text="싼 답")], [_resp(text="강한 답")])
    msgs = _msgs(
        {"role": "assistant", "content": None},
        {"role": "tool", "tool_call_id": "a", "content": "오류: 그런 도구 없음"},
        {"role": "tool", "tool_call_id": "b", "content": "인자 검증 실패. x: 필수"},
    )
    out = r.complete(msgs, None, step=3)
    assert out.route == "tool_errors:2" and out.model == "openai:gpt-5-mini" and cheap.seen == []
    # 오류 사이에 정상 결과가 끼면 연속이 아니다
    r2, cheap2, _ = _router([_resp(text="싼 답")], [])
    ok = _msgs(
        {"role": "tool", "tool_call_id": "a", "content": "오류: 하나"},
        {"role": "tool", "tool_call_id": "b", "content": '{"rows": []}'},
        {"role": "tool", "tool_call_id": "c", "content": "오류: 둘"},
    )
    assert r2.complete(ok, None, step=3).model == "local" and len(cheap2.seen) == 1


def test_step_threshold_and_sticky_until_new_run():
    r, cheap, strong = _router(
        [_resp(text="싼1"), _resp(text="싼2")], [_resp(text="강1"), _resp(text="강2")], escalate_after_steps=6
    )
    assert r.complete(_msgs(), None, step=6).model == "local"
    assert r.complete(_msgs(), None, step=7).route == "steps:7"
    assert r.complete(_msgs(), None, step=2).model == "openai:gpt-5-mini"  # 같은 실행(키 같음)이면 계속 강한 모델
    assert r.complete(_msgs(), None, step=1).model == "local"  # 새 실행은 초기화


def test_make_provider_parses_route_spec(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    r = make_provider("route:local->openai:gpt-5-mini")
    assert isinstance(r, RoutingProvider) and r.cheap_spec == "local" and r.strong_spec == "openai:gpt-5-mini"
    assert "gpt-5-mini" in r.model and r.wants_step and r.supports_stream
    assert isinstance(make_provider("route"), RoutingProvider)
    with pytest.raises(ValueError, match="route 형식"):
        make_provider("route:local")


def test_loop_records_model_and_route_per_call():
    cheap = FakeProvider([_resp(calls=[("add", {"a": 2, "b": 3})], parse_error="bad json"), _resp(text="싼 끝")])
    strong = FakeProvider(
        [
            LLMResponse(tool_calls=[tool_call("add", a=2, b=3)], usage=Usage(prompt_tokens=50, completion_tokens=5)),
            _resp(text="답은 5", p=60, c=6),
        ]
    )
    r = RoutingProvider(cheap, strong, "local", "openai:gpt-5-mini")
    res = Agent(r, make_registry(), "sys").run("2+3?")
    llm = [e for e in res.events if e.kind == "llm"]
    assert res.answer == "답은 5" and llm[0].data["route"] == "cheap_response:parse_error"
    assert [e.data["model"] for e in llm] == ["openai:gpt-5-mini", "openai:gpt-5-mini"]  # 올라간 뒤엔 끝까지 강한 모델
    assert res.usage.by_model["openai:gpt-5-mini"]["calls"] == 2 and res.usage.by_model["local"]["calls"] == 1
    assert res.usage.prompt_tokens == 10 + 50 + 60
