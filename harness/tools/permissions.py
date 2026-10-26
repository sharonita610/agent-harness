"""권한 정책과 승인 핸들러. 등급(read/write/destructive) → 허용 / 승인 필요 / 거부."""
from __future__ import annotations

from enum import StrEnum
from typing import Callable

from pydantic import BaseModel

from .registry import Tier, Tool


class Decision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


DEFAULT_TIER_POLICY: dict[Tier, Decision] = {
    Tier.READ: Decision.ALLOW,
    Tier.WRITE: Decision.REQUIRE_APPROVAL,
    Tier.DESTRUCTIVE: Decision.DENY,
}

ApprovalHandler = Callable[[Tool, BaseModel], bool]


class Policy:
    def __init__(self, tier_policy: dict[Tier, Decision] | None = None, tool_overrides: dict[str, Decision] | None = None):
        self.tier_policy = {**DEFAULT_TIER_POLICY, **(tier_policy or {})}
        self.tool_overrides = tool_overrides or {}

    def decide(self, tool: Tool, args: BaseModel | None = None) -> Decision:
        if tool.name in self.tool_overrides:
            return self.tool_overrides[tool.name]
        return self.tier_policy[tool.tier]


def auto_deny(tool: Tool, args: BaseModel) -> bool:
    return False


def auto_approve(tool: Tool, args: BaseModel) -> bool:
    return True


def cli_approval(tool: Tool, args: BaseModel) -> bool:
    """터미널에서 y/n 으로 승인. 서비스 버전에서는 SSE 이벤트 + 승인 API 로 대체된다."""
    print(f"\n[승인 요청] 도구 '{tool.name}' ({tool.tier}) 실행 인자: {args.model_dump()}")
    ans = input("실행할까요? (y/N) ").strip().lower()
    return ans in ("y", "yes")


def describe(decision: Decision) -> str:
    return {Decision.ALLOW: "허용", Decision.REQUIRE_APPROVAL: "사람 승인 필요", Decision.DENY: "차단"}[decision]
