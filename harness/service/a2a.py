"""A2A(Agent-to-Agent) 프로토콜 서버 면. 팩 서비스 하나를 A2A 에이전트로 노출한다.

구현 범위(A2A 0.2/0.3 의 핵심 부분집합):
- Agent Card: GET /.well-known/agent-card.json (0.3) · /.well-known/agent.json (0.2 호환)
- JSON-RPC 2.0: POST /a2a — message/send(blocking 지원) · message/stream(SSE) · tasks/get · tasks/cancel
- 태스크 = 우리 실행(run), contextId = 세션. 상태 매핑: queued→submitted, running→working, 결재 대기→input-required,
  done→completed, failed→failed, cancelled→canceled. 답변은 text 아티팩트, 도구가 만든 그림(url)은 file 아티팩트.
사람 결재는 A2A 로 넘기지 않는다: 하위 비서의 결재는 그 비서 화면에서 사람이 하고, 상위 에이전트는 input-required 를 보고 기다린다.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .sse import ping

PROTOCOL_VERSION = "0.3.0"
TERMINAL = {"completed", "failed", "canceled", "rejected"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text_message(role: str, text: str, task_id: str | None = None, context_id: str | None = None) -> dict[str, Any]:
    return {"kind": "message", "role": role, "parts": [{"kind": "text", "text": text}], "messageId": "msg_" + uuid.uuid4().hex[:12],
            **({"taskId": task_id} if task_id else {}), **({"contextId": context_id} if context_id else {})}


def _err(rid: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    e: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": e}


class A2AFacade:
    def __init__(self, state, pack, base_url: str | None = None):
        self.state = state
        self.pack = pack
        self.base_url = base_url

    # ---------------------------------------------------------------- agent card
    def card(self, request_base: str) -> dict[str, Any]:
        pk = self.pack
        base = (self.base_url or request_base).rstrip("/")
        skills = [{"id": f"example-{i + 1}", "name": label, "description": text, "tags": [pk.name], "examples": [text]}
                  for i, (label, text) in enumerate(pk.examples)]
        tool_names = self.state.registry.names()
        skills.append({"id": "tools", "name": "도구 목록", "description": "이 비서가 함수 호출로 쓰는 도구: " + ", ".join(tool_names), "tags": ["tools"]})
        return {
            "protocolVersion": PROTOCOL_VERSION, "name": pk.title, "description": pk.description, "url": f"{base}/a2a", "version": "0.1.0",
            "preferredTransport": "JSONRPC", "provider": {"organization": "agent-harness", "url": "https://github.com/sharonita610/agent-harness"},
            "capabilities": {"streaming": True, "pushNotifications": False, "stateTransitionHistory": False},
            "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain", "image/svg+xml"],
            "securitySchemes": {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}} if self.state.users.required else {},
            "security": [{"apiKey": []}] if self.state.users.required else [],
            "skills": skills, "metadata": {"pack": pk.name, "aliases": pk.aliases, "tools": tool_names, "approvalNote": "write 등급 도구는 사람 결재 후 실행된다. 결재 대기 중엔 태스크가 input-required 상태가 된다."},
        }

    # ---------------------------------------------------------------- task view
    def _task(self, run: dict[str, Any], request_base: str, history_length: int | None = None) -> dict[str, Any]:
        store = self.state.store
        rid = run["id"]
        pending = [a for a in store.list_approvals("pending", 200) if a["run_id"] == rid]
        state = {"queued": "submitted", "running": "working", "done": "completed", "failed": "failed", "cancelled": "canceled"}.get(run["status"], "unknown")
        status: dict[str, Any] = {"state": state, "timestamp": _now()}
        if state == "working" and pending:
            a = pending[0]
            status["state"] = "input-required"
            status["message"] = _text_message("agent", f"사람 결재 대기: 도구 '{a['tool']}' ({a['tier']}) 인자 {json.dumps(a['args'], ensure_ascii=False)[:200]}. "
                                              f"결재는 이 비서의 화면 또는 POST /approvals/{a['id']} 로 한다.", rid, run["session_id"])
        elif state == "failed":
            status["message"] = _text_message("agent", run.get("error") or "실행 실패", rid, run["session_id"])
        artifacts: list[dict[str, Any]] = []
        if state == "completed":
            artifacts.append({"artifactId": f"{rid}-answer", "name": "answer", "parts": [{"kind": "text", "text": run.get("answer") or ""}]})
            base = (self.base_url or request_base).rstrip("/")
            for e in store.events(rid):
                url = e["data"].get("url") if e["kind"] == "tool_result" else None
                if isinstance(url, str):
                    mime = "image/svg+xml" if url.endswith(".svg") else "application/octet-stream"
                    artifacts.append({"artifactId": f"{rid}-{e['seq']}", "name": e["data"].get("name", "file"),
                                      "parts": [{"kind": "file", "file": {"uri": base + url, "mimeType": mime}}]})
        history = [_text_message("user", run.get("question") or "", rid, run["session_id"])]
        if state == "completed":
            history.append(_text_message("agent", run.get("answer") or "", rid, run["session_id"]))
        if history_length is not None:
            history = history[-history_length:] if history_length > 0 else []
        return {"kind": "task", "id": rid, "contextId": run["session_id"], "status": status, "artifacts": artifacts, "history": history,
                "metadata": {"pack": self.pack.name, "steps": run.get("steps"), "latency_s": run.get("latency_s"), "stopped_reason": run.get("stopped_reason")}}

    def _extract_text(self, message: dict[str, Any]) -> str:
        parts = message.get("parts") or []
        texts = [p.get("text", "") for p in parts if p.get("kind", "text") == "text" and p.get("text")]
        return "\n".join(texts).strip()

    # ---------------------------------------------------------------- methods
    def send(self, params: dict[str, Any], user, request_base: str) -> dict[str, Any]:
        msg = params.get("message") or {}
        text = self._extract_text(msg)
        if not text:
            raise ValueError("message.parts 에 text 가 필요합니다")
        store = self.state.store
        sid = msg.get("contextId")
        if not sid or not store.get_session(sid):
            sid = store.create_session(self.state.default_provider, title="a2a")["id"]
        run_id = self.state.jobs.submit(sid, text, None, user=user)
        store.add_audit(getattr(user, "name", "a2a"), getattr(user, "role", "?"), "a2a.send", run_id, {"question": text[:200]})
        cfg = params.get("configuration") or {}
        if cfg.get("blocking"):
            deadline = time.monotonic() + float(cfg.get("timeoutSeconds") or 180)
            while time.monotonic() < deadline:
                run = store.get_run(run_id)
                task = self._task(run, request_base)
                if task["status"]["state"] in TERMINAL or task["status"]["state"] == "input-required":
                    return task
                time.sleep(0.3)
        return self._task(store.get_run(run_id), request_base)

    def get(self, params: dict[str, Any], request_base: str) -> dict[str, Any] | None:
        run = self.state.store.get_run(str(params.get("id") or ""))
        return self._task(run, request_base, params.get("historyLength")) if run else None

    def cancel(self, params: dict[str, Any], request_base: str) -> dict[str, Any] | None:
        rid = str(params.get("id") or "")
        run = self.state.store.get_run(rid)
        if not run:
            return None
        if not self.state.jobs.cancel(rid):
            raise PermissionError("취소할 수 없는 상태")
        return self._task(self.state.store.get_run(rid), request_base)

    async def stream(self, rpc_id: Any, params: dict[str, Any], user, request_base: str):
        """message/stream: 태스크 생성 후 저장된 이벤트를 폴링해 status-update / artifact-update 를 SSE 로 보낸다."""
        store = self.state.store
        task = self.send(params, user, request_base)
        rid, cid = task["id"], task["contextId"]
        yield f"data: {json.dumps({'jsonrpc': '2.0', 'id': rpc_id, 'result': task}, ensure_ascii=False)}\n\n"
        last_seq, last_state, idle = 0, task["status"]["state"], 0.0
        while True:
            await asyncio.sleep(0.4)
            idle += 0.4
            events = store.events(rid, last_seq)
            for e in events:
                last_seq = e["seq"]
                if e["kind"] == "tool_call":
                    msg = _text_message("agent", f"도구 호출: {e['data'].get('name')}", rid, cid)
                    yield f"data: {json.dumps({'jsonrpc': '2.0', 'id': rpc_id, 'result': {'kind': 'status-update', 'taskId': rid, 'contextId': cid, 'status': {'state': 'working', 'message': msg, 'timestamp': _now()}, 'final': False}}, ensure_ascii=False)}\n\n"
                    idle = 0.0
            run = store.get_run(rid)
            t = self._task(run, request_base)
            st = t["status"]["state"]
            if st != last_state:
                last_state = st
                yield f"data: {json.dumps({'jsonrpc': '2.0', 'id': rpc_id, 'result': {'kind': 'status-update', 'taskId': rid, 'contextId': cid, 'status': t['status'], 'final': st in TERMINAL}}, ensure_ascii=False)}\n\n"
                idle = 0.0
            if st in TERMINAL:
                for art in t["artifacts"]:
                    yield f"data: {json.dumps({'jsonrpc': '2.0', 'id': rpc_id, 'result': {'kind': 'artifact-update', 'taskId': rid, 'contextId': cid, 'artifact': art, 'lastChunk': True}}, ensure_ascii=False)}\n\n"
                break
            if idle >= 15:
                yield ping()
                idle = 0.0


def mount_a2a(app: FastAPI, state, pack, current_user, require) -> None:
    facade = A2AFacade(state, pack)

    def _base(request: Request) -> str:
        import os
        return os.getenv("HARNESS_PUBLIC_URL") or str(request.base_url).rstrip("/")

    @app.get("/.well-known/agent-card.json")
    @app.get("/.well-known/agent.json")
    def agent_card(request: Request):
        return facade.card(_base(request))

    @app.post("/a2a")
    async def a2a_rpc(request: Request, user=Depends(current_user)):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(_err(None, -32700, "Parse error"), status_code=200)
        rid = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}
        if body.get("jsonrpc") != "2.0" or not method:
            return JSONResponse(_err(rid, -32600, "Invalid Request"))
        base = _base(request)
        try:
            if method in ("message/send", "message/stream"):
                if not user.has("operator"):
                    return JSONResponse(_err(rid, -32001, "operator 역할이 필요합니다"), status_code=403)
                if method == "message/stream":
                    return StreamingResponse(facade.stream(rid, params, user, base), media_type="text/event-stream",
                                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
                loop = asyncio.get_running_loop()
                task = await loop.run_in_executor(None, facade.send, params, user, base)
                return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": task})
            if method == "tasks/get":
                task = facade.get(params, base)
                return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": task} if task else _err(rid, -32001, "Task not found", {"id": params.get("id")}))
            if method == "tasks/cancel":
                task = facade.cancel(params, base)
                return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": task} if task else _err(rid, -32001, "Task not found"))
            if method in ("tasks/pushNotificationConfig/set", "tasks/pushNotificationConfig/get", "tasks/resubscribe"):
                return JSONResponse(_err(rid, -32004, "This operation is not supported"))
            return JSONResponse(_err(rid, -32601, f"Method not found: {method}"))
        except ValueError as e:
            return JSONResponse(_err(rid, -32602, str(e)))
        except PermissionError as e:
            return JSONResponse(_err(rid, -32002, str(e)))
