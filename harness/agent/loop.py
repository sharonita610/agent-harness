"""에이전트 루프: 계획 → 도구 호출 → 결과 검증 → 답변. 프레임워크 없이 직접 구현."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from pydantic import ValidationError

from ..tools.permissions import ApprovalHandler, Decision, Policy, auto_deny
from ..tools.progress import progress_scope
from ..tools.registry import ToolRegistry, format_validation_error
from .context import compact, truncate_result
from .providers import Provider, salvage_text_tool_calls
from .types import AgentEvent, AgentResult, ToolCall, Usage

EventSink = Callable[[AgentEvent], None]


class Agent:
    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
        system_prompt: str,
        policy: Policy | None = None,
        approval_handler: ApprovalHandler = auto_deny,
        max_steps: int = 12,
        max_tool_errors: int = 4,
        result_max_chars: int = 4000,
        context_budget_tokens: int = 24000,
        max_nudges: int = 1,
        max_calls_per_step: int = 4,
        max_truncation_retries: int = 2,
        on_event: EventSink | None = None,
    ):
        self.provider = provider
        self.registry = registry
        self.system_prompt = system_prompt
        self.policy = policy or Policy()
        self.approval_handler = approval_handler
        self.max_steps = max_steps
        self.max_tool_errors = max_tool_errors
        self.result_max_chars = result_max_chars
        self.context_budget_tokens = context_budget_tokens
        self.max_nudges = max_nudges
        self.max_truncation_retries = max_truncation_retries
        self.max_calls_per_step = max_calls_per_step
        self.on_event = on_event
        names = "|".join(re.escape(n) for n in registry.names()) or "__none__"
        # 소형 모델 실패 모드: 도구를 호출하지 않고 SQL/함수 호출을 '글로' 쓰는 경우를 잡는 패턴
        # 도구를 '글로만' 쓴 신호: 도구 이름 호출 꼴, SQL 문. 코드 펜스 자체는 신호가 아니다(문법 설명 답변에 코드가 들어가는 건 정상).
        self._text_tool_pattern = re.compile(rf"\b(?:{names})\s*\(|\bSELECT\b[\s\S]{{0,400}}\bFROM\b", re.I)

    # ------------------------------------------------------------------
    def run(
        self,
        user_message: str,
        history: list[dict[str, Any]] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> AgentResult:
        t0 = time.perf_counter()
        events: list[AgentEvent] = []
        tool_log: list[dict[str, Any]] = []
        approvals: list[dict[str, Any]] = []
        usage = Usage()
        tool_errors = 0
        nudges = 0
        truncations = 0

        def emit(kind: str, step: int, **data: Any) -> None:
            ev = AgentEvent(kind=kind, step=step, data=data)  # type: ignore[arg-type]
            events.append(ev)
            if self.on_event:
                self.on_event(ev)

        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": user_message})
        schemas = self.registry.schemas()

        answer = ""
        stopped = "max_steps"
        step = 0
        for step in range(1, self.max_steps + 1):
            if should_cancel and should_cancel():
                stopped = "cancelled"
                answer = "요청에 따라 작업을 취소했습니다."
                emit("stopped", step, reason=stopped)
                break
            emit("step", step)
            messages = compact(messages, self.context_budget_tokens)
            t_call = time.perf_counter()
            extra = {"step": step} if getattr(self.provider, "wants_step", False) else {}
            try:
                if getattr(self.provider, "supports_stream", False):
                    resp = self.provider.complete(
                        messages, schemas, on_delta=lambda piece, _s=step: emit("delta", _s, text=piece), **extra
                    )
                else:
                    resp = self.provider.complete(messages, schemas, **extra)
            except Exception as e:  # 네트워크/프로바이더 오류
                emit("stopped", step, reason="provider_error", error=str(e))
                return self._result(
                    f"모델 호출 오류: {e}", step, "provider_error", tool_log, approvals, events, usage, t0, messages
                )
            usage.add(resp.usage, model=resp.model or getattr(self.provider, "model", None))
            emit(
                "llm",
                step,
                text=resp.text,
                tool_calls=[tc.model_dump() for tc in resp.tool_calls],
                finish=resp.finish_reason,
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                latency_s=round(time.perf_counter() - t_call, 2),
                **({"model": resp.model} if resp.model else {}),
                **({"route": resp.route} if resp.route else {}),
            )

            calls = resp.tool_calls or salvage_text_tool_calls(resp.text)
            calls = self._drop_garbage_calls(calls, step, emit)
            calls, dropped_note = self._dedupe_and_cap(calls, step, emit)
            if not calls:
                text = (resp.text or "").strip()
                if not text and resp.finish_reason == "length" and truncations < self.max_truncation_retries:
                    # 추론(reasoning) 토큰이 출력 예산을 다 써서 본문이 빈 경우(gpt-oss 류): 짧게 가라고 한 번 되묻는다
                    truncations += 1
                    messages.append(
                        {
                            "role": "user",
                            "content": "출력이 길이 제한에 걸려 답이 비었습니다. 길게 생각하지 말고, 필요한 도구를 바로 함수 호출로 실행하거나 짧게 답하세요.",
                        }
                    )
                    emit("nudge", step, reason="truncated_empty", completion_tokens=resp.usage.completion_tokens)
                    continue
                if nudges < self.max_nudges and self._text_tool_pattern.search(text):
                    # 도구를 글로만 쓴 경우: 한 번 되묻어 실제 호출을 유도한다
                    nudges += 1
                    messages.append({"role": "assistant", "content": text})
                    messages.append(
                        {
                            "role": "user",
                            "content": "SQL 이나 함수 호출을 글로 적지 말고, 제공된 도구를 실제 함수 호출(tool call)로 실행해서 결과를 확인한 뒤 답하세요.",
                        }
                    )
                    emit("nudge", step, reason="text_tool_pattern", text_head=text[:200])
                    continue
                answer = text
                stopped = "final"
                emit("final", step, answer=answer)
                break

            messages.append(self._assistant_message(resp.text if not resp.tool_calls else None, calls))
            for i, call in enumerate(calls):
                content, ok = self._execute(call, step, emit, tool_log, approvals)
                if not ok:
                    tool_errors += 1
                if dropped_note and i == len(calls) - 1:
                    content += "\n\n" + dropped_note
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            if tool_errors > self.max_tool_errors:
                stopped = "too_many_tool_errors"
                answer = "도구 호출 오류가 반복되어 작업을 중단했습니다. 질문을 더 구체적으로 주시거나 다른 방법을 알려주세요."
                emit("stopped", step, reason=stopped)
                break
        else:
            # 스텝 소진: 도구 없이 마무리 답변 한 번
            messages.append(
                {
                    "role": "user",
                    "content": "지금까지 확인한 내용만으로 최종 답변을 간결하게 정리해 주세요. 도구는 더 쓰지 마세요.",
                }
            )
            try:
                resp = self.provider.complete(messages, None)
                usage.add(resp.usage, model=resp.model or getattr(self.provider, "model", None))
                answer = (resp.text or "").strip()
            except Exception as e:
                answer = f"스텝 한도 도달 후 마무리 호출 실패: {e}"
            emit("stopped", step, reason="max_steps")

        return self._result(answer, step, stopped, tool_log, approvals, events, usage, t0, messages)

    # ------------------------------------------------------------------
    def _execute(self, call: ToolCall, step: int, emit, tool_log: list, approvals: list) -> tuple[str, bool]:
        """도구 하나 실행. (모델에게 돌려줄 문자열, 정상 여부)"""
        emit("tool_call", step, id=call.id, name=call.name, arguments=call.arguments, parse_error=call.parse_error)
        entry: dict[str, Any] = {"step": step, "name": call.name, "arguments": call.arguments, "status": "ok"}
        tool_log.append(entry)

        tool = self.registry.get(call.name)
        if tool is None:
            entry["status"] = "unknown_tool"
            msg = f"오류: '{call.name}' 이라는 도구는 없습니다. 사용 가능한 도구: {', '.join(self.registry.names())}"
            emit("tool_error", step, name=call.name, error=msg)
            return msg, False
        if call.parse_error:
            entry["status"] = "parse_error"
            msg = f"오류: {call.parse_error}. 인자는 JSON 객체로 보내세요."
            emit("tool_error", step, name=call.name, error=msg)
            return msg, False
        try:
            args = tool.validate(call.arguments)
        except ValidationError as e:
            entry["status"] = "validation_error"
            msg = format_validation_error(e)
            emit("tool_error", step, name=call.name, error=msg)
            return msg, False

        decision = self.policy.decide(tool, args)
        if decision == Decision.DENY:
            entry["status"] = "denied"
            emit("denied", step, name=call.name, tier=str(tool.tier))
            return (
                f"차단됨: '{tool.name}' 은(는) {tool.tier} 등급 도구로 이 에이전트에서는 실행할 수 없습니다. "
                "사용자에게 담당자가 직접 처리해야 한다고 안내하세요."
            ), True
        if decision == Decision.REQUIRE_APPROVAL:
            emit("approval_request", step, name=call.name, arguments=args.model_dump(), tier=str(tool.tier))
            approved = bool(self.approval_handler(tool, args))
            approvals.append({"step": step, "name": call.name, "arguments": args.model_dump(), "approved": approved})
            emit("approval_result", step, name=call.name, approved=approved)
            if not approved:
                entry["status"] = "not_approved"
                return (
                    "승인되지 않음: 사용자가 이 변경을 승인하지 않았습니다. 변경을 실행하지 말고, "
                    "승인이 필요하다는 사실과 대안을 안내하세요."
                ), True
        last = {"t": 0.0}

        def _progress(done: int, total: int, label: str) -> None:
            # 긴 도구(스윕 등)의 진행률. 0.4초에 한 번 이상은 저장하지 않되 마지막 점은 꼭 남긴다
            now = time.monotonic()
            if done >= total or now - last["t"] >= 0.4:
                last["t"] = now
                emit("progress", step, name=call.name, done=done, total=total, label=label)

        try:
            with progress_scope(_progress):
                result = tool.handler(args)
        except Exception as e:  # 도구 실행 자체의 오류
            entry["status"] = "exec_error"
            msg = f"도구 실행 오류({type(e).__name__}): {e}"
            emit("tool_error", step, name=call.name, error=msg)
            return msg, False
        content = truncate_result(result, self.result_max_chars)
        entry["result_preview"] = content[:300]
        extra = {"url": result["url"]} if isinstance(result, dict) and isinstance(result.get("url"), str) else {}
        if isinstance(result, (dict, list)) and len(content) <= self.result_max_chars:
            extra["result"] = (
                result  # 화면이 표·수치·출처 카드로 그릴 수 있게 원본 구조를 함께 보낸다(모델에게 가는 내용과 같은 범위)
            )
        emit("tool_result", step, name=call.name, preview=content[:300], chars=len(content), **extra)
        return content, True

    def _dedupe_and_cap(self, calls: list[ToolCall], step: int, emit) -> tuple[list[ToolCall], str | None]:
        """같은 이름+인자의 중복 호출은 한 번만 실행하고, 한 스텝의 호출 수를 제한한다.
        소형 모델이 같은 호출을 수십 번 반복 생성해 컨텍스트를 폭발시키는 실패 모드를 막는다."""
        seen: set[str] = set()
        unique: list[ToolCall] = []
        dup = 0
        for c in calls:
            key = c.name + json.dumps(c.arguments, sort_keys=True, ensure_ascii=False)
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            unique.append(c)
        over = max(0, len(unique) - self.max_calls_per_step)
        unique = unique[: self.max_calls_per_step]
        note = None
        if dup or over:
            emit(
                "tool_error",
                step,
                name="(batch)",
                error=f"중복 호출 {dup}개 제거, 초과 호출 {over}개 제외 (스텝당 최대 {self.max_calls_per_step})",
            )
            note = (f"[시스템] 같은 도구 호출이 {dup}번 반복되어 한 번만 실행했습니다. " if dup else "") + (
                f"[시스템] 한 스텝에 도구 호출은 최대 {self.max_calls_per_step}개입니다. {over}개는 실행하지 않았으니 필요하면 다음 스텝에서 호출하세요."
                if over
                else ""
            )
        return unique, note

    @staticmethod
    def _drop_garbage_calls(calls: list[ToolCall], step: int, emit) -> list[ToolCall]:
        """유효한 호출이 하나 이상 있으면, 인자가 '{' 처럼 잘린 쓰레기 호출은 조용히 버린다(소형 모델의 중복 생성)."""
        good = [c for c in calls if not c.parse_error]
        if not good or len(good) == len(calls):
            return calls
        for c in calls:
            if c.parse_error:
                emit("tool_error", step, name=c.name, error=f"잘린 중복 호출 무시: {c.parse_error[:80]}")
        return good

    @staticmethod
    def _assistant_message(text: str | None, calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
                }
                for c in calls
            ],
        }

    @staticmethod
    def _result(answer, steps, stopped, tool_log, approvals, events, usage, t0, messages) -> AgentResult:
        return AgentResult(
            answer=answer,
            steps=steps,
            stopped_reason=stopped,
            tool_calls=tool_log,
            approvals=approvals,
            events=events,
            usage=usage,
            latency_s=round(time.perf_counter() - t0, 2),
            messages=messages,
        )
