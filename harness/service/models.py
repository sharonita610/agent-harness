"""API 요청/응답 스키마."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    provider: str | None = None
    title: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    provider: str | None = None


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "deny"]
    by: str = "web"


class JobCreate(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    provider: str | None = None
