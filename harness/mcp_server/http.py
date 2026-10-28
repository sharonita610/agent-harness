"""MCP 를 HTTP(streamable) 로도 연다: 서비스 포트의 `/mcp`. 웹 API 와 같은 키·역할·데이터 범위를 쓰고, 변경 도구는 같은 결재함을 거친다.

- 인증: Authorization: Bearer <키> (또는 X-API-Key). 사용자 파일이 있으면 필수, 없으면 개발 모드(익명 admin).
- 범위: 사용자 범위가 있으면 그 범위의 도구 레지스트리(임시 뷰로 잘린 DB)를 쓴다. 웹 채팅과 같은 경로.
- 결재: write 등급 도구는 실행 전에 결재 브로커에 전표를 올리고 승인될 때까지 기다린다(실행 기록 kind=mcp). 결재함에 뜨고, 반려되면 실행하지 않는다.
- destructive 는 노출하지 않는다. 감사 기록에 누가 어떤 도구를 불렀는지 남는다.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from ..packs import Pack
from ..service.auth import User, UserStore
from ..tools.permissions import Decision, Policy
from .enterprise_server import add_doc_resource, tool_annotations, wrap_tool

_current_user: ContextVar[User | None] = ContextVar("harness_mcp_user", default=None)


class MCPAuth:
    """ASGI 미들웨어: 키를 확인해 사용자를 contextvar 에 두고 요청을 넘긴다."""

    def __init__(self, app: ASGIApp, users: UserStore):
        self.app = app
        self.users = users

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-api-key")
        user = self.users.authenticate(key)
        if user is None:
            if self.users.required:
                body = json.dumps({"error": "API 키가 필요합니다. Authorization: Bearer <키> 로 보내세요."}).encode()
                await send(
                    {"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]}
                )
                await send({"type": "http.response.body", "body": body})
                return
            user = User("dev", "admin", anonymous=True)
        token = _current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user.reset(token)


class MountedMCP:
    """마운트된 MCP 면. 앱 lifespan 마다 `async with mounted.lifespan():` 로 세션 관리자를 새로 만든다.

    SDK 의 세션 관리자는 한 번만 run() 할 수 있어서, 테스트처럼 앱을 여러 번 켜고 끄려면 켤 때마다 HTTP 앱을 다시 만든다.
    """

    def __init__(self, server: MCPServer, make_http_app):
        self.server = server
        self._make = make_http_app
        self.inner = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.inner is None:
            raise RuntimeError("MCP 면이 아직 켜지지 않았습니다(앱 lifespan 밖)")
        await self.inner(scope, receive, send)

    @asynccontextmanager
    async def lifespan(self):
        self.inner = self._make()
        async with self.server.session_manager.run():
            yield
        self.inner = None


def mount_mcp(app, state, pack: Pack, policy: Policy | None = None, path: str = "/mcp") -> MountedMCP:
    """서비스 앱에 /mcp 를 붙인다. 돌려주는 객체의 lifespan() 을 앱 lifespan 안에서 열어야 한다."""
    policy = policy or Policy()
    store = state.store
    session_box: dict[str, str] = {}

    def mcp_session() -> str:
        if "id" not in session_box:
            session_box["id"] = store.create_session(state.default_provider, title="MCP 호출", user=None)["id"]
        return session_box["id"]

    def make_call(read_only: bool):
        async def call(name: str, kwargs: dict[str, Any]) -> Any:
            user = _current_user.get() or User("dev", "admin", anonymous=True)
            if not user.has_role("operator"):
                raise ToolError(f"'{name}' 은(는) operator 이상만 부를 수 있습니다. 현재 역할: {user.role}")
            tool = state.registry_for(user).get(name)
            if read_only:
                try:
                    result = await anyio.to_thread.run_sync(tool.run, kwargs)
                except Exception as e:  # 도구의 오류 문장(범위 밖, 형식 오류)을 그대로 클라이언트에 보여 준다
                    raise ToolError(f"{type(e).__name__}: {e}") from e
                store.add_audit(user.name, user.role, "mcp_call", name, {"args": kwargs})
                return result
            # 변경 도구: 결재함을 거친다. 웹 채팅의 결재와 같은 브로커·저장소라 결재함에 그대로 뜬다
            t0 = time.perf_counter()
            rid = store.create_run(
                mcp_session(),
                "mcp",
                f"[MCP · {user.name}] {name} {json.dumps(kwargs, ensure_ascii=False)[:120]}",
                user=user.name,
            )
            store.start_run(rid)
            seq = 0

            def ev(kind: str, data: dict[str, Any]) -> None:
                nonlocal seq
                seq += 1
                store.add_event(rid, seq, kind, None, data)

            ev("tool_call", {"name": name, "arguments": kwargs, "via": "mcp", "user": user.name})
            aid = state.broker.request(rid, name, kwargs, str(tool.tier))
            ev(
                "approval_pending",
                {
                    "approval_id": aid,
                    "tool": name,
                    "arguments": kwargs,
                    "tier": str(tool.tier),
                    "timeout_s": state.broker.timeout_s,
                },
            )
            store.add_audit(user.name, user.role, "mcp_approval_requested", name, {"approval_id": aid, "run_id": rid})
            gone = threading.Event()
            try:
                approved = await anyio.to_thread.run_sync(state.broker.wait, aid, gone.is_set, abandon_on_cancel=True)
            except anyio.get_cancelled_exc_class():
                # 클라이언트가 기다리다 끊었다: 전표를 닫고 실행을 취소로 남긴다(승인돼도 실행되지 않게)
                gone.set()
                state.broker.cancel_pending(rid, by="disconnected")
                ev("stopped", {"reason": "cancelled", "error": "MCP 클라이언트 연결 끊김"})
                store.finish_run(rid, "cancelled", error="MCP 클라이언트가 결재를 기다리다 끊음")
                raise
            ev("approval_result", {"name": name, "approved": approved})
            usage = SimpleNamespace(prompt_tokens=0, completion_tokens=0)
            if not approved:
                store.finish_run(
                    rid,
                    "failed",
                    result=SimpleNamespace(
                        answer="결재가 승인되지 않아 실행하지 않았습니다.",
                        stopped_reason="not_approved",
                        steps=1,
                        latency_s=round(time.perf_counter() - t0, 2),
                        usage=usage,
                    ),
                    error="결재 반려 또는 기한 만료",
                )
                return {
                    "approved": False,
                    "approval_id": aid,
                    "run_id": rid,
                    "note": "결재자가 승인하지 않았거나 기한이 지났습니다. 실행하지 않았습니다.",
                }
            try:
                result = await anyio.to_thread.run_sync(tool.run, kwargs)
            except Exception as e:
                ev("tool_error", {"name": name, "error": f"{type(e).__name__}: {e}"})
                store.finish_run(rid, "failed", error=f"{type(e).__name__}: {e}")
                raise ToolError(f"{type(e).__name__}: {e}") from e
            content = json.dumps(result, ensure_ascii=False, default=str)
            ev("tool_result", {"name": name, "preview": content[:300], "chars": len(content), "result": result})
            answer = f"{name} 실행 완료 (결재 {aid} 승인)"
            ev("final", {"answer": answer})
            store.add_audit(user.name, user.role, "mcp_call", name, {"args": kwargs, "approval_id": aid, "run_id": rid})
            store.finish_run(
                rid,
                "done",
                result=SimpleNamespace(
                    answer=answer,
                    stopped_reason="done",
                    steps=1,
                    latency_s=round(time.perf_counter() - t0, 2),
                    usage=usage,
                ),
            )
            return {"approved": True, "approval_id": aid, "run_id": rid, "result": result}

        return call

    server = MCPServer(
        pack.mcp_name,
        instructions=(
            pack.mcp_instructions + " 변경 도구는 결재함에서 승인된 뒤에 실행된다(승인 전에는 호출이 기다린다)."
        ),
    )
    for tool in state.registry:
        decision = policy.decide(tool)
        if decision == Decision.DENY:
            continue
        read_only = decision == Decision.ALLOW
        server.add_tool(
            wrap_tool(tool, make_call(read_only), is_async=True),
            name=tool.name,
            description=tool.description + ("" if read_only else " [변경 작업: 결재함 승인 후 실행]"),
            annotations=tool_annotations(read_only),
        )
    add_doc_resource(server, pack.docs_dir)
    # Host 헤더 검사(DNS 리바인딩 방어)는 HARNESS_MCP_ALLOWED_HOSTS 를 줄 때만 켠다. 키 인증이 있고, 기본값(127.0.0.1만 허용)은
    # 역프록시·테스트 호스트를 막아 버리기 때문이다.
    allowed = [h.strip() for h in os.getenv("HARNESS_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(allowed), allowed_hosts=allowed, allowed_origins=[]
    )
    mounted = MountedMCP(
        server,
        lambda: server.streamable_http_app(
            streamable_http_path="/", json_response=True, stateless_http=True, transport_security=security
        ),
    )
    app.mount(path, MCPAuth(mounted, state.users))  # 주소는 {path}/ (끝 슬래시). 없이 부르면 307 로 보낸다
    return mounted
