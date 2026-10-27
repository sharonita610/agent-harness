"""도메인 팩 인터페이스와 선택.

플랫폼(에이전트 루프·권한·서비스·평가·트레이스)은 팩 내부를 모른다. 팩은 아래 Pack 한 묶음만 제공하면 된다.
선택은 HARNESS_PACK 환경변수(기본 enterprise) 또는 CLI --pack / create_app(pack=...) 로 한다.
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PACK_NAMES = ("enterprise", "spice")
DEFAULT_PACK = "enterprise"


@dataclass
class Pack:
    name: str
    title: str                                        # 사람용 이름(화면 머리말·README)
    description: str
    build_tools: Callable[..., list]                  # (scope=None, index=None, db_path=..., **kw) -> list[Tool]
    system_prompts: dict[str, str]                    # {"v1": ..., "v2": ...}
    parse_scope: Callable[[Any], Any]                 # str | dict | None -> Scope | None (범위 개념이 없으면 항상 None)
    scope_note: Callable[[Any], str]                  # 프롬프트에 붙이는 범위 안내
    shared_kwargs: Callable[[], dict[str, Any]]       # 서비스가 한 번 만들어 모든 레지스트리에 공유하는 자원(문서 인덱스 등)
    tasks_path: Path                                  # 평가 태스크 yaml
    docs_dir: Path                                    # MCP 리소스·검색 평가용 문서 폴더
    tool_labels: dict[str, str] = field(default_factory=dict)   # 화면 타임라인용 한국어 라벨
    files: dict[str, Path] = field(default_factory=dict)        # /files/<key> 로 서빙할 폴더(그래프 등)
    seed: Callable[[], Any] | None = None             # 데이터 준비(없으면 None)
    mcp_name: str = "agent-harness"
    mcp_instructions: str = ""
    welcome: str = ""                                 # 화면 머리말 한 줄
    examples: list[list[str]] = field(default_factory=list)  # 화면 예시 [라벨, 질문]
    empty_hint: str = ""                              # 대화가 비었을 때 안내

    @property
    def system_prompt(self) -> str:
        return self.system_prompts["v1"]


def current_pack_name() -> str:
    return os.getenv("HARNESS_PACK", DEFAULT_PACK)


def load_pack(name: str | None = None) -> Pack:
    name = (name or current_pack_name()).strip().lower()
    if name not in PACK_NAMES:
        raise ValueError(f"알 수 없는 팩: {name!r}. 가능한 팩: {', '.join(PACK_NAMES)}")
    mod = importlib.import_module(f"harness.packs.{name}.pack")
    return mod.PACK
