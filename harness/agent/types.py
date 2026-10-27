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

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens


class LLMResponse(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = None


EventKind = Literal[
    "step", "llm", "delta", "nudge", "tool_call", "tool_result", "tool_error",
    "approval_request", "approval_result", "denied", "final", "stopped",
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
