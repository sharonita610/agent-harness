"""에이전트 루프: 계획 → 도구 호출 → 결과 검증 → 답변. 프레임워크 없이 직접 구현."""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from pydantic import ValidationError

from ..tools.permissions import ApprovalHandler, Decision, Policy, auto_deny
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
        self.on_event = on_event

    # ------------------------------------------------------------------
    def run(self, user_message: str, history: list[dict[str, Any]] | None = None) -> AgentResult:
        t0 = time.perf_counter()
        events: list[AgentEvent] = []
        tool_log: list[dict[str, Any]] = []
        approvals: list[dict[str, Any]] = []
        usage = Usage()
        tool_errors = 0

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
            emit("step", step)
            messages = compact(messages, self.context_budget_tokens)
            try:
                resp = self.provider.complete(messages, schemas)
            except Exception as e:  # 네트워크/프로바이더 오류
                emit("stopped", step, reason="provider_error", error=str(e))
                return self._result(f"모델 호출 오류: {e}", step, "provider_error", tool_log, approvals, events, usage, t0, messages)
            usage.add(resp.usage)
            emit("llm", step, text=resp.text, tool_calls=[tc.model_dump() for tc in resp.tool_calls], finish=resp.finish_reason)

            calls = resp.tool_calls or salvage_text_tool_calls(resp.text)
            if not calls:
                answer = (resp.text or "").strip()
                stopped = "final"
                emit("final", step, answer=answer)
                break

            messages.append(self._assistant_message(resp.text if not resp.tool_calls else None, calls))
            for call in calls:
                content, ok = self._execute(call, step, emit, tool_log, approvals)
                if not ok:
                    tool_errors += 1
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            if tool_errors > self.max_tool_errors:
                stopped = "too_many_tool_errors"
                answer = "도구 호출 오류가 반복되어 작업을 중단했습니다. 질문을 더 구체적으로 주시거나 다른 방법을 알려주세요."
                emit("stopped", step, reason=stopped)
                break
        else:
            # 스텝 소진: 도구 없이 마무리 답변 한 번
            messages.append({"role": "user", "content": "지금까지 확인한 내용만으로 최종 답변을 간결하게 정리해 주세요. 도구는 더 쓰지 마세요."})
            try:
                resp = self.provider.complete(messages, None)
                usage.add(resp.usage)
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
            return (f"차단됨: '{tool.name}' 은(는) {tool.tier} 등급 도구로 이 에이전트에서는 실행할 수 없습니다. "
                    "사용자에게 담당자가 직접 처리해야 한다고 안내하세요."), True
        if decision == Decision.REQUIRE_APPROVAL:
            emit("approval_request", step, name=call.name, arguments=args.model_dump(), tier=str(tool.tier))
            approved = bool(self.approval_handler(tool, args))
            approvals.append({"step": step, "name": call.name, "arguments": args.model_dump(), "approved": approved})
            emit("approval_result", step, name=call.name, approved=approved)
            if not approved:
                entry["status"] = "not_approved"
                return ("승인되지 않음: 사용자가 이 변경을 승인하지 않았습니다. 변경을 실행하지 말고, "
                        "승인이 필요하다는 사실과 대안을 안내하세요."), True
        try:
            result = tool.handler(args)
        except Exception as e:  # 도구 실행 자체의 오류
            entry["status"] = "exec_error"
            msg = f"도구 실행 오류({type(e).__name__}): {e}"
            emit("tool_error", step, name=call.name, error=msg)
            return msg, False
        content = truncate_result(result, self.result_max_chars)
        entry["result_preview"] = content[:300]
        emit("tool_result", step, name=call.name, preview=content[:300], chars=len(content))
        return content, True

    @staticmethod
    def _assistant_message(text: str | None, calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                for c in calls
            ],
        }

    @staticmethod
    def _result(answer, steps, stopped, tool_log, approvals, events, usage, t0, messages) -> AgentResult:
        return AgentResult(answer=answer, steps=steps, stopped_reason=stopped, tool_calls=tool_log, approvals=approvals,
                           events=events, usage=usage, latency_s=round(time.perf_counter() - t0, 2), messages=messages)
