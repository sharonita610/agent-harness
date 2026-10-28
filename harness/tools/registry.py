"""도구(툴) 정의와 레지스트리. 모든 인자는 pydantic 으로 검증한다."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Iterable

from pydantic import BaseModel, ValidationError


class Tier(StrEnum):
    READ = "read"  # 조회. 자유롭게 실행
    WRITE = "write"  # 데이터 변경. 사람 승인 필요
    DESTRUCTIVE = "destructive"  # 삭제 등 되돌릴 수 없는 변경. 기본 차단


def _strip_titles(schema: Any) -> Any:
    """pydantic 스키마의 title 필드를 제거해 소형 모델이 덜 헷갈리게 한다."""
    if isinstance(schema, dict):
        schema.pop("title", None)
        for v in schema.values():
            _strip_titles(v)
    elif isinstance(schema, list):
        for v in schema:
            _strip_titles(v)
    return schema


@dataclass
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    handler: Callable[[BaseModel], Any]
    tier: Tier = Tier.READ

    def schema(self) -> dict[str, Any]:
        params = _strip_titles(self.params.model_json_schema())
        params.setdefault("type", "object")
        params.setdefault("properties", {})
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": params},
        }

    def validate(self, raw_args: dict[str, Any]) -> BaseModel:
        return self.params.model_validate(raw_args)

    def run(self, raw_args: dict[str, Any]) -> Any:
        return self.handler(self.validate(raw_args))


def format_validation_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        parts.append(f"{loc}: {err.get('msg')}")
    return "인자 검증 실패. " + "; ".join(parts) + ". 스키마에 맞게 인자를 고쳐 다시 호출하세요."


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        for t in tools:
            self.add(t)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"중복 도구 이름: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)
