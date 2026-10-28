"""팩의 도구를 MCP 서버로 노출한다 (mcp 2.x, stdio). 기본 팩은 enterprise(회사 데이터 비서), `harness mcp --pack spice` 로 회로 팩.

같은 Tool 레지스트리를 쓰므로 CLI 에이전트와 MCP 클라이언트(Claude Desktop, Hermes Agent 등)가 똑같은 도구를 본다.
- read 도구: readOnlyHint=True
- write 도구: 그대로 노출하되 readOnlyHint=False (MCP 클라이언트 쪽 사람이 실행 전 확인한다)
- destructive 도구: 기본 정책이 차단이므로 노출하지 않는다

사용: uv run harness mcp      (Claude Desktop 설정 예시는 README 참고)
"""

from __future__ import annotations

import inspect
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from pydantic_core import PydanticUndefined

from ..packs import Pack, load_pack
from ..tools.permissions import Decision, Policy
from ..tools.registry import Tool, ToolRegistry


def _wrap(tool: Tool):
    """pydantic 인자 모델 → 명시적 키워드 인자 시그니처를 가진 함수. MCP 가 스키마를 뽑을 수 있게 한다."""
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

    def fn(**kwargs: Any) -> Any:
        return tool.run(kwargs)

    fn.__name__ = tool.name
    fn.__doc__ = tool.description
    fn.__signature__ = inspect.Signature(params, return_annotation=Any)  # type: ignore[attr-defined]
    fn.__annotations__ = {**annotations, "return": Any}
    return fn


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
            _wrap(tool),
            name=tool.name,
            description=tool.description + ("" if read_only else " [변경 작업: 실행 전 사용자 확인 필요]"),
            annotations=ToolAnnotations(
                read_only_hint=read_only, destructive_hint=False, idempotent_hint=read_only, open_world_hint=False
            ),
        )

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

    return server


def main(pack: str | None = None) -> None:
    build_server(pack=pack).run(transport="stdio")


if __name__ == "__main__":
    main()
