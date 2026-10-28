"""팩의 도구를 MCP 서버로 노출한다 (mcp 2.x). 이 모듈은 stdio(같은 컴퓨터의 클라이언트). HTTP 는 http.py 가 서비스 포트의 /mcp 에 연다. 기본 팩은 enterprise(회사 데이터 비서), `harness mcp --pack spice` 로 회로 팩.

같은 Tool 레지스트리를 쓰므로 CLI 에이전트와 MCP 클라이언트(Claude Desktop, Hermes Agent 등)가 똑같은 도구를 본다.
- read 도구: readOnlyHint=True
- write 도구: 그대로 노출하되 readOnlyHint=False (MCP 클라이언트 쪽 사람이 실행 전 확인한다)
- destructive 도구: 기본 정책이 차단이므로 노출하지 않는다

사용: uv run harness mcp      (Claude Desktop 설정 예시는 README 참고)
"""

from __future__ import annotations

import inspect
from typing import Annotated, Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from pydantic_core import PydanticUndefined

from ..packs import Pack, load_pack
from ..tools.permissions import Decision, Policy
from ..tools.registry import Tool, ToolRegistry


def wrap_tool(tool: Tool, call: Callable[[str, dict[str, Any]], Any] | None = None, is_async: bool = False):
    """pydantic 인자 모델 → 명시적 키워드 인자 시그니처를 가진 함수. MCP 가 스키마를 뽑을 수 있게 한다.

    call(name, kwargs) 를 주면 실행을 그쪽에 맡긴다(HTTP 면은 사용자 범위·결재를 거친다). 없으면 도구를 바로 돌린다(stdio).
    """
    params = []
    annotations: dict[str, Any] = {}
    for name, field in tool.params.model_fields.items():
        default = inspect.Parameter.empty
        if field.default is not PydanticUndefined:
            default = field.default
        elif field.default_factory is not None:
            default = field.default_factory()  # type: ignore[call-arg]
        # default_factory 가 있는 필드(list 기본값 등)는 계산한 기본값과 함께 넘기면 pydantic 이 거부하므로 설명만 남긴 FieldInfo 로 바꾼다
        ann = Annotated[
            field.annotation, Field(description=field.description) if field.default_factory is not None else field
        ]
        annotations[name] = ann
        params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=ann))

    if is_async:

        async def handler(**kwargs: Any) -> Any:  # type: ignore[misc]
            return await call(tool.name, kwargs)  # type: ignore[misc]

    else:

        def handler(**kwargs: Any) -> Any:
            return call(tool.name, kwargs) if call else tool.run(kwargs)

    handler.__name__ = tool.name
    handler.__doc__ = tool.description
    handler.__signature__ = inspect.Signature(params, return_annotation=Any)  # type: ignore[attr-defined]
    handler.__annotations__ = {**annotations, "return": Any}
    return handler


def build_server(
    registry: ToolRegistry | None = None, policy: Policy | None = None, pack: Pack | str | None = None
) -> MCPServer:
    pk = pack if isinstance(pack, Pack) else load_pack(pack)
    registry = registry or ToolRegistry(pk.build_tools())
    policy = policy or Policy()
    docs_dir = pk.docs_dir
    server = MCPServer(pk.mcp_name, instructions=pk.mcp_instructions)
    for tool in registry:
        decision = policy.decide(tool)
        if decision == Decision.DENY:
            continue
        read_only = decision == Decision.ALLOW
        server.add_tool(
            wrap_tool(tool),
            name=tool.name,
            description=tool.description + ("" if read_only else " [변경 작업: 실행 전 사용자 확인 필요]"),
            annotations=ToolAnnotations(
                read_only_hint=read_only, destructive_hint=False, idempotent_hint=read_only, open_world_hint=False
            ),
        )

    add_doc_resource(server, docs_dir)
    return server


def tool_annotations(read_only: bool) -> ToolAnnotations:
    return ToolAnnotations(
        read_only_hint=read_only, destructive_hint=False, idempotent_hint=read_only, open_world_hint=False
    )


def add_doc_resource(server: MCPServer, docs_dir) -> None:
    """팩 문서 폴더를 docs://{name} 리소스로."""

    @server.resource(
        "docs://{name}",
        name="reference_doc",
        description="참고 문서 원문(마크다운). name 은 파일명(확장자 제외).",
        mime_type="text/markdown",
    )
    def read_doc(name: str) -> str:
        if "/" in name or ".." in name:
            raise ValueError("잘못된 이름")
        path = docs_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"문서 없음: {name}. 목록: {[p.stem for p in sorted(docs_dir.glob('*.md'))]}")
        return path.read_text(encoding="utf-8")


def main(pack: str | None = None) -> None:
    build_server(pack=pack).run(transport="stdio")


if __name__ == "__main__":
    main()
