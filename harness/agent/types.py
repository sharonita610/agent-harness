"""에이전트 코어가 주고받는 자료형."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    parse_error: str | None = None  # 모델이 JSON 이 아닌 인자를 보낸 경우 원문


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_model: dict[str, dict[str, int]] = Field(default_factory=dict)  # 라우팅 때 모델별 토큰·호출 수(비용 계산용)

    def add(self, other: "Usage", model: str | None = None) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        if other.by_model:
            for m, u in other.by_model.items():
                slot = self.by_model.setdefault(m, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
                for k in slot:
                    slot[k] += u.get(k, 0)
        elif model:
            slot = self.by_model.setdefault(model, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
            slot["prompt_tokens"] += other.prompt_tokens
            slot["completion_tokens"] += other.completion_tokens
            slot["calls"] += 1


class LLMResponse(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None
    model: str | None = None  # 실제로 답한 모델(라우팅 프로바이더가 채움)
    route: str | None = None  # 강한 모델로 올린 이유(라우팅 때만)


EventKind = Literal[
    "step",
    "llm",
    "delta",
    "nudge",
    "tool_call",
    "tool_result",
    "tool_error",
    "progress",
    "approval_request",
    "approval_result",
    "denied",
    "final",
    "stopped",
]


class AgentEvent(BaseModel):
    kind: EventKind
    step: int
    data: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    answer: str
    steps: int
    stopped_reason: str  # final | max_steps | too_many_tool_errors | provider_error
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    events: list[AgentEvent] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_s: float = 0.0
    messages: list[dict[str, Any]] = Field(default_factory=list)
