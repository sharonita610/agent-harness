"""harmony 원문(gpt-oss) 에서 도구 호출·최종 답 건지기(ADR-029: 서버가 도구 호출을 파싱하지 않는 엔진용)."""

from __future__ import annotations

from harness.agent.providers import _with_harmony, parse_harmony
from harness.agent.types import LLMResponse

RAW_CALL = (
    "<|channel|>analysis<|message|>The user asks how many delayed orders. We need to call run_sql.<|end|>"
    "<|start|>assistant<|channel|>commentary to=functions.run_sql<|channel|>commentary <|constrain|>json<|message|>"
    '{"sql":"SELECT COUNT(*) FROM orders WHERE status = \'지연\'" }'
)
RAW_FINAL = "<|channel|>analysis<|message|>Count is 255.<|end|><|start|>assistant<|channel|>final<|message|>현재 지연 주문은 **255건**입니다.<|return|>"


def test_parse_harmony_extracts_call_and_drops_analysis():
    text, calls = parse_harmony(RAW_CALL)
    assert text is None and len(calls) == 1
    assert calls[0].name == "run_sql" and calls[0].arguments == {
        "sql": "SELECT COUNT(*) FROM orders WHERE status = '지연'"
    }
    assert calls[0].parse_error is None


def test_parse_harmony_final_channel_becomes_text():
    text, calls = parse_harmony(RAW_FINAL)
    assert text == "현재 지연 주문은 **255건**입니다." and calls == []


def test_parse_harmony_passthrough_and_analysis_only():
    assert parse_harmony("그냥 답") == ("그냥 답", [])
    assert parse_harmony(None) == (None, [])
    text, calls = parse_harmony("<|channel|>analysis<|message|>생각만 했다<|end|>")
    assert calls == [] and text == "생각만 했다"


def test_provider_postprocess_only_when_no_structured_calls():
    r = _with_harmony(LLMResponse(text=RAW_CALL))
    assert r.text is None and r.tool_calls[0].name == "run_sql"
    plain = _with_harmony(LLMResponse(text="답", tool_calls=[]))
    assert plain.text == "답" and plain.tool_calls == []


def test_analysis_only_truncated_response_is_treated_as_empty():
    looped = "<|channel|>analysis<|message|>We must not mention the tool. We must not mention the tool. We must"
    r = _with_harmony(LLMResponse(text=looped, finish_reason="length"))
    assert r.text is None and r.tool_calls == []  # 루프의 되묻기·라우팅의 올리기 신호가 된다
    r2 = _with_harmony(LLMResponse(text=looped, finish_reason="stop"))
    assert r2.text is not None  # 길이 제한이 아니면 마지막 조각을 답으로


def test_extra_body_env_for_local_and_compat(monkeypatch):
    from harness.agent.providers import make_provider

    monkeypatch.setenv("HARNESS_LLM_EXTRA_BODY", '{"repetition_penalty": 1.1, "repetition_context_size": 64}')
    p = make_provider("compat:http://127.0.0.1:8091/v1|m")
    assert p.extra_body == {"repetition_penalty": 1.1, "repetition_context_size": 64}
    assert make_provider("local").extra_body["repetition_penalty"] == 1.1
    monkeypatch.setenv("HARNESS_LLM_EXTRA_BODY", "nope")
    import pytest

    with pytest.raises(ValueError, match="JSON"):
        make_provider("local")
