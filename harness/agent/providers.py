"""LLM 프로바이더 추상화. 설정 한 줄로 로컬(llama.cpp) / OpenAI / Anthropic 교체."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from typing import Any, Callable, Protocol

from ..offline import check_provider_kind, check_url
from .types import LLMResponse, ToolCall, Usage

DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_LOCAL_MODEL = "local-llm"

_TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)


OnDelta = Callable[[str], None]


class Provider(Protocol):
    name: str
    model: str

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse: ...


def wait_ready(base_url: str = DEFAULT_LOCAL_BASE_URL, timeout_s: float = 180, on_wait=None) -> bool:
    """llama-server 류 로컬 서버의 /health 가 ok 가 될 때까지 기다린다. 모델 로딩(503) 중 호출하는 실수를 막는다."""
    health = base_url.rstrip("/").removesuffix("/v1") + "/health"
    t0 = time.monotonic()
    notified = False
    while time.monotonic() - t0 < timeout_s:
        try:
            with urllib.request.urlopen(health, timeout=3) as r:
                if json.load(r).get("status") == "ok":
                    return True
        except Exception:
            pass
        if on_wait and not notified:
            on_wait()
            notified = True
        time.sleep(2)
    return False


def _parse_args(raw: Any) -> tuple[dict[str, Any], str | None]:
    if isinstance(raw, dict):
        return raw, None
    if raw is None or raw == "":
        return {}, None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed, None
        return {}, f"인자가 JSON 객체가 아님: {str(raw)[:200]}"
    except json.JSONDecodeError as e:
        return {}, f"인자 JSON 파싱 실패({e.msg}): {str(raw)[:200]}"


def _calls_from_obj(obj: Any, start: int) -> list[ToolCall]:
    """{"name","arguments"} 객체, 그 리스트, 또는 {"tool_call(s)": [...]} 래퍼에서 호출을 뽑는다."""
    if isinstance(obj, dict):
        for key in ("tool_calls", "tool_call", "calls"):
            if isinstance(obj.get(key), list):
                return _calls_from_obj(obj[key], start)
            if isinstance(obj.get(key), dict):
                return _calls_from_obj([obj[key]], start)
        obj = [obj]
    out: list[ToolCall] = []
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                args, err = _parse_args(item.get("arguments", item.get("parameters", {})))
                out.append(ToolCall(id=f"salvaged_{start + i}", name=item["name"], arguments=args, parse_error=err))
    return out


def salvage_text_tool_calls(text: str | None) -> list[ToolCall]:
    """소형 모델이 툴콜을 본문 텍스트로 뱉은 경우 건져낸다.
    형태 1: <tool_call>{"name":..,"arguments":..}</tool_call>
    형태 2: ```json {"tool_call":[{"name":..,"arguments":..}]} ``` 또는 ```json {"name":..,"arguments":..} ```
    """
    if not text:
        return []
    calls: list[ToolCall] = []
    for m in _TOOL_CALL_TAG.finditer(text):
        try:
            calls += _calls_from_obj(json.loads(m.group(1)), len(calls))
        except json.JSONDecodeError:
            continue
    if calls:
        return calls
    for m in _JSON_FENCE.finditer(text):
        try:
            calls += _calls_from_obj(json.loads(m.group(1)), len(calls))
        except json.JSONDecodeError:
            continue
    return calls


def assemble_stream(chunks: Any, on_delta: OnDelta | None = None) -> LLMResponse:
    """스트리밍 청크(choices[0].delta) 를 모아 일반 응답과 같은 LLMResponse 로 만든다. 텍스트 조각은 on_delta 로 즉시 흘린다."""
    text_parts: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    finish = None
    usage = Usage()
    for ch in chunks:
        u = getattr(ch, "usage", None)
        if u:
            usage = Usage(
                prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(u, "completion_tokens", 0) or 0,
            )
        if not getattr(ch, "choices", None):
            continue
        c0 = ch.choices[0]
        finish = getattr(c0, "finish_reason", None) or finish
        d = getattr(c0, "delta", None)
        if d is None:
            continue
        if getattr(d, "content", None):
            text_parts.append(d.content)
            if on_delta:
                on_delta(d.content)
        for tc in getattr(d, "tool_calls", None) or []:
            idx = getattr(tc, "index", 0) or 0
            slot = calls.setdefault(idx, {"id": None, "name": "", "args": ""})
            if getattr(tc, "id", None):
                slot["id"] = tc.id
            fn = getattr(tc, "function", None)
            if fn is not None:
                if getattr(fn, "name", None):
                    slot["name"] += fn.name
                if getattr(fn, "arguments", None):
                    slot["args"] += fn.arguments
    tool_calls: list[ToolCall] = []
    for idx in sorted(calls):
        slot = calls[idx]
        args, err = _parse_args(slot["args"])
        tool_calls.append(ToolCall(id=slot["id"] or f"call_{idx}", name=slot["name"], arguments=args, parse_error=err))
    return LLMResponse(text="".join(text_parts) or None, tool_calls=tool_calls, usage=usage, finish_reason=finish)


class OpenAICompatProvider:
    """OpenAI 호환 /v1/chat/completions. llama-server, vLLM, LM Studio, OpenAI 모두 여기로. on_delta 를 주면 스트리밍."""

    supports_stream = True

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        extra_body: dict | None = None,
        name: str = "openai-compat",
    ):
        from openai import OpenAI

        self.name = name
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_body = extra_body or {}
        # OpenAI 의 추론 모델(gpt-5·o 시리즈)은 max_tokens 대신 max_completion_tokens 를 받고 temperature 를 바꿀 수 없다
        self.reasoning_api = name == "openai" and model.lower().startswith(("gpt-5", "o1", "o3", "o4"))
        self._client = OpenAI(
            base_url=base_url, api_key=api_key or os.getenv("OPENAI_API_KEY") or "no-key", timeout=300
        )

    def complete(self, messages, tools=None, on_delta: OnDelta | None = None) -> LLMResponse:
        from openai import NOT_GIVEN, APIStatusError

        for attempt in range(60):  # 모델 로딩 중(503) 이면 최대 ~2분 기다린다
            try:
                if on_delta is not None:
                    return assemble_stream(self._create(messages, tools, NOT_GIVEN, stream=True), on_delta)
                resp = self._create(messages, tools, NOT_GIVEN)
                break
            except APIStatusError as e:
                if e.status_code >= 500 and e.status_code != 503 and attempt < 1:
                    # llama-server 가 모델 출력을 도구 호출 문법으로 못 읽은 경우("does not match the expected … format") 등. 샘플링이 달라지므로 한 번 더 시도
                    time.sleep(0.5)
                    continue
                if e.status_code == 503 and attempt < 59:
                    time.sleep(2)
                    continue
                raise
        choice = resp.choices[0]
        msg = choice.message
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            args, err = _parse_args(tc.function.arguments)
            calls.append(
                ToolCall(id=tc.id or f"call_{len(calls)}", name=tc.function.name, arguments=args, parse_error=err)
            )
        usage = Usage()
        if resp.usage:
            usage = Usage(
                prompt_tokens=resp.usage.prompt_tokens or 0, completion_tokens=resp.usage.completion_tokens or 0
            )
        return LLMResponse(text=msg.content, tool_calls=calls, usage=usage, finish_reason=choice.finish_reason)

    def _create(self, messages, tools, not_given, stream: bool = False):
        kw: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            tools=tools if tools else not_given,
            tool_choice="auto" if tools else not_given,
            extra_body=self.extra_body or None,
        )
        if self.reasoning_api:
            kw["max_completion_tokens"] = self.max_tokens
        else:
            kw["temperature"] = self.temperature
            kw["max_tokens"] = self.max_tokens
        if stream:
            kw["stream"] = True
            kw["stream_options"] = {"include_usage": True}
        return self._client.chat.completions.create(**kw)


class AnthropicProvider:
    """Anthropic Messages API. OpenAI 형식 메시지/툴 스키마를 변환해서 보낸다."""

    def __init__(self, model: str, api_key: str | None = None, temperature: float = 0.2, max_tokens: int = 2048):
        import anthropic

        self.name = "anthropic"
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY"))

    @staticmethod
    def _convert(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        system = None
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "system":
                system = (system + "\n\n" if system else "") + m["content"]
            elif role == "user":
                out.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []) or []:
                    fn = tc["function"]
                    try:
                        inp = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        inp = {}
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": fn["name"], "input": inp})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif role == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m.get("content") or ""}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return system, out

    def complete(self, messages, tools=None) -> LLMResponse:
        system, converted = self._convert(messages)
        a_tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
            }
            for t in (tools or [])
        ]
        kwargs: dict[str, Any] = dict(
            model=self.model, max_tokens=self.max_tokens, temperature=self.temperature, messages=converted
        )
        if system:
            kwargs["system"] = system
        if a_tools:
            kwargs["tools"] = a_tools
        resp = self._client.messages.create(**kwargs)
        text_parts, calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        usage = Usage(prompt_tokens=resp.usage.input_tokens, completion_tokens=resp.usage.output_tokens)
        return LLMResponse(
            text="\n".join(text_parts) or None, tool_calls=calls, usage=usage, finish_reason=resp.stop_reason
        )


class RoutingProvider:
    """모델 라우팅: 싼 모델(보통 로컬)로 돌리다가 "막혔다" 는 신호가 보이면 그 호출부터 강한 모델로 올린다(ADR-024).

    신호 셋, 전부 대화 내용과 스텝 번호에서만 읽는다(별도 분류기 없음):
    - 도구 오류 연속: 직전 도구 결과가 연달아 `escalate_after_errors` 개 이상 오류("오류: …")
    - 스텝 소진 조짐: 한 실행에서 `escalate_after_steps` 스텝을 넘겨도 답이 안 나옴(루프가 step 을 넘겨준다)
    - 응답 품질: 싼 모델의 응답이 비었거나(길이 제한) 도구 인자가 JSON 이 아님 → 같은 메시지로 강한 모델에 다시 묻는다
    한 번 올라가면 그 실행이 끝날 때까지 강한 모델이 맡는다(step 1 에서 초기화). 토큰은 모델별로 따로 센다.
    """

    name = "route"

    def __init__(
        self,
        cheap: Provider,
        strong: Provider,
        cheap_spec: str = "local",
        strong_spec: str = "openai",
        escalate_after_errors: int = 2,
        escalate_after_steps: int = 6,
    ):
        self.cheap, self.strong = cheap, strong
        self.cheap_spec, self.strong_spec = cheap_spec, strong_spec
        self.escalate_after_errors = escalate_after_errors
        self.escalate_after_steps = escalate_after_steps
        self.model = f"{getattr(cheap, 'model', cheap_spec)} → {getattr(strong, 'model', strong_spec)}"
        self.supports_stream = bool(getattr(cheap, "supports_stream", False))
        self.wants_step = True
        self._sticky: dict[str, str] = {}  # 대화 키 → 올라간 이유(실행이 끝날 때까지 유지)

    # ---- 신호
    @staticmethod
    def _key(messages: list[dict[str, Any]]) -> str:
        first_user = next((m for m in messages if m.get("role") == "user"), None)
        return str(hash((messages[0].get("content") if messages else None, first_user and first_user.get("content"))))

    @staticmethod
    def _is_error(content: Any) -> bool:
        text = str(content or "").lstrip()
        return text.startswith(("오류", "도구 실행 오류", "인자 검증 실패")) or "오류" in text[:24]

    def trailing_tool_errors(self, messages: list[dict[str, Any]]) -> int:
        n = 0
        for m in reversed(messages):
            role = m.get("role")
            if role == "tool":
                if self._is_error(m.get("content")):
                    n += 1
                else:
                    break
            elif role == "user":
                break
        return n

    def decide(self, messages: list[dict[str, Any]], step: int | None) -> str | None:
        key = self._key(messages)
        if step == 1:
            self._sticky.pop(key, None)
        if key in self._sticky:
            return self._sticky[key]
        errs = self.trailing_tool_errors(messages)
        if errs >= self.escalate_after_errors:
            return f"tool_errors:{errs}"
        if step is not None and step > self.escalate_after_steps:
            return f"steps:{step}"
        return None

    @staticmethod
    def _weak_response(resp: LLMResponse) -> str | None:
        if any(tc.parse_error for tc in resp.tool_calls):
            return "parse_error"
        if not (resp.text or "").strip() and not resp.tool_calls:
            return "empty" if resp.finish_reason != "length" else "truncated"
        return None

    # ---- 호출
    def _call(self, prov: Provider, messages, tools, on_delta) -> LLMResponse:
        if on_delta is not None and getattr(prov, "supports_stream", False):
            return prov.complete(messages, tools, on_delta=on_delta)  # type: ignore[call-arg]
        return prov.complete(messages, tools)

    def complete(self, messages, tools=None, on_delta: OnDelta | None = None, step: int | None = None) -> LLMResponse:
        key = self._key(messages)
        reason = self.decide(messages, step)
        if reason:
            self._sticky[key] = reason
            resp = self._call(self.strong, messages, tools, on_delta)
            resp.usage = _attributed(resp.usage, self.strong_spec)
            resp.model, resp.route = self.strong_spec, reason
            return resp
        cheap = self._call(self.cheap, messages, tools, on_delta)
        weak = self._weak_response(cheap)
        if weak is None:
            cheap.usage = _attributed(cheap.usage, self.cheap_spec)
            cheap.model = self.cheap_spec
            return cheap
        reason = f"cheap_response:{weak}"
        self._sticky[key] = reason
        strong = self._call(
            self.strong, messages, tools, None
        )  # 스트리밍으로 이미 흘린 조각이 있어도 답은 강한 모델 것
        usage = _attributed(cheap.usage, self.cheap_spec)
        usage.add(strong.usage, model=self.strong_spec)
        strong.usage = usage
        strong.model, strong.route = self.strong_spec, reason
        return strong


def _attributed(usage: Usage, model: str) -> Usage:
    out = Usage()
    out.add(usage, model=model)
    return out


def make_provider(spec: str | None = None, **kw) -> Provider:
    """spec 예: "local" | "local:모델별칭" | "openai:gpt-5-mini" | "anthropic:claude-sonnet-4-5" | "compat:http://host:port/v1|model"
    | "route" | "route:local->openai:gpt-5-mini" (싼 모델 -> 막히면 올릴 모델)"""
    spec = spec or os.getenv("HARNESS_PROVIDER", "local")
    kind, _, rest = spec.partition(":")
    check_provider_kind(kind, spec)  # 오프라인 모드면 openai:/anthropic: 는 여기서 거부
    if kind == "route":
        cheap_spec, sep, strong_spec = (rest or "local->openai:gpt-5-mini").partition("->")
        if not sep or not cheap_spec or not strong_spec:
            raise ValueError(f"route 형식: route:<싼 모델>-><강한 모델> (받은 값: {spec!r})")
        return RoutingProvider(
            make_provider(cheap_spec, **kw), make_provider(strong_spec, **kw), cheap_spec, strong_spec
        )
    if kind == "local":
        return OpenAICompatProvider(
            model=rest or os.getenv("LLM_MODEL", DEFAULT_LOCAL_MODEL),
            base_url=check_url(os.getenv("LLM_BASE_URL", DEFAULT_LOCAL_BASE_URL), "모델 서버"),
            api_key="llama.cpp",
            name="local",
            **kw,
        )
    if kind == "openai":
        return OpenAICompatProvider(model=rest or "gpt-5-mini", name="openai", **kw)
    if kind == "anthropic":
        return AnthropicProvider(model=rest or "claude-sonnet-4-5", **kw)
    if kind == "compat":
        base_url, _, model = rest.partition("|")
        return OpenAICompatProvider(
            model=model or DEFAULT_LOCAL_MODEL,
            base_url=check_url(base_url, "모델 서버"),
            api_key="no-key",
            name="compat",
            **kw,
        )
    raise ValueError(f"알 수 없는 프로바이더: {spec}")
