"""FastAPI 앱. uv run uvicorn harness.service.app:app --port 8787"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..agent.providers import make_provider
from ..packs import Pack, load_pack
from ..tools.permissions import Policy, describe
from ..tools.registry import ToolRegistry
from .auth import User, UserStore, make_dependencies
from .broker import ApprovalBroker
from .jobs import JobQueue
from .models import ApprovalDecision, ChatRequest, JobCreate, SessionCreate
from .runner import stream_chat
from .store import Store

STATIC = Path(__file__).parent / "static"
UI_DIST = Path(__file__).resolve().parents[2] / "ui" / "dist"
DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "service.db"


@dataclass
class AppState:
    store: Store
    broker: ApprovalBroker
    registry: ToolRegistry
    system_prompt: str
    provider_factory: Callable[[str], Any]
    default_provider: str = "local"
    max_steps: int = 12
    jobs: JobQueue | None = field(default=None)
    users: UserStore | None = field(default=None)
    registry_factory: Callable[[Any], ToolRegistry] | None = field(default=None)
    pack: Pack | None = field(default=None)
    _scoped: dict[str, ToolRegistry] = field(default_factory=dict)

    def scope_of(self, user):
        spec = getattr(user, "scope", None) if user is not None else None
        return self.pack.parse_scope(spec) if self.pack else None

    def registry_for(self, user) -> ToolRegistry:
        """범위가 없으면 공용 레지스트리, 있으면 범위별로 만들어 캐시."""
        scope = self.scope_of(user)
        if scope is None or self.registry_factory is None:
            return self.registry
        key = str(sorted(scope.to_dict().items()))
        if key not in self._scoped:
            self._scoped[key] = self.registry_factory(scope)
        return self._scoped[key]

    def system_prompt_for(self, user) -> str:
        scope = self.scope_of(user)
        return self.system_prompt + (self.pack.scope_note(scope) if self.pack else "")


def create_app(db_path: str | Path | None = None, provider_factory: Callable[[str], Any] | None = None,
               registry: ToolRegistry | None = None, system_prompt: str | None = None,
               approval_timeout_s: float = 600, default_provider: str | None = None, job_concurrency: int = 1,
               users: UserStore | None = None, model_probe: Callable[[], bool] | None = None,
               kwargs_registry_factory: Callable[[Any], ToolRegistry] | None = None, pack: str | Pack | None = None) -> FastAPI:
    pk = pack if isinstance(pack, Pack) else load_pack(pack)
    registry_factory = None
    if registry is None or system_prompt is None:
        shared = pk.shared_kwargs()
        registry = registry or ToolRegistry(pk.build_tools(**shared))
        system_prompt = system_prompt or pk.system_prompt
        registry_factory = lambda scope: ToolRegistry(pk.build_tools(scope=scope, **shared))
    store = Store(db_path or os.getenv("HARNESS_SERVICE_DB", str(DEFAULT_DB)))
    state = AppState(store=store, broker=ApprovalBroker(store, approval_timeout_s), registry=registry, system_prompt=system_prompt,
                     provider_factory=provider_factory or make_provider,
                     default_provider=default_provider or os.getenv("HARNESS_PROVIDER", "local"))
    state.pack = pk
    state.jobs = JobQueue(state, concurrency=job_concurrency)
    state.registry_factory = registry_factory or kwargs_registry_factory
    state.users = users or UserStore.load()
    current_user, require = make_dependencies(state.users)

    def _default_probe() -> bool:
        """local/compat 프로바이더면 /health 를 1초 안에 확인. 호스팅 API 는 항상 True."""
        spec = state.default_provider
        if spec.startswith("local"):
            base = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1")
        elif spec.startswith("compat:"):
            base = spec.split(":", 1)[1].split("|")[0]
        else:
            return True
        import json as _json
        import urllib.request
        try:
            with urllib.request.urlopen(base.rstrip("/").removesuffix("/v1") + "/health", timeout=1) as r:
                return _json.load(r).get("status") == "ok"
        except Exception:
            return False

    probe = model_probe or _default_probe
    probe_cache = {"at": 0.0, "ok": False}

    def model_status() -> dict[str, Any]:
        if time.monotonic() - probe_cache["at"] > 10:
            probe_cache["ok"] = probe()
            probe_cache["at"] = time.monotonic()
        hosted = not (state.default_provider.startswith("local") or state.default_provider.startswith("compat:"))
        return {"provider": state.default_provider, "reachable": probe_cache["ok"], "hosted": hosted}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.fail_stale()
        await state.jobs.start()
        yield
        await state.jobs.stop()

    app = FastAPI(title="agent-harness service", version="0.2.0", lifespan=lifespan)
    app.state.harness = state
    policy = Policy()

    def _session(sid: str | None, provider: str | None) -> str:
        if sid:
            if not store.get_session(sid):
                raise HTTPException(404, "세션 없음")
            return sid
        return store.create_session(provider or state.default_provider)["id"]

    @app.get("/health")
    def health():
        return {"status": "ok", "tools": len(registry), "provider": state.default_provider, "pending_approvals": len(state.broker.pending()),
                "auth_required": state.users.required, "model": model_status(),
                "pack": {"name": pk.name, "title": pk.title, "description": pk.description, "welcome": pk.welcome, "examples": pk.examples,
                         "empty_hint": pk.empty_hint, "tool_labels": pk.tool_labels}, "a2a": {"card": "/.well-known/agent-card.json", "rpc": "/a2a"}}

    @app.get("/me")
    def me(user: User = Depends(current_user)):
        sc = state.scope_of(user)
        return {"name": user.name, "role": user.role, "anonymous": user.anonymous, "auth_required": state.users.required, "pack": pk.name,
                "scope": sc.to_dict() if sc else None, "scope_text": sc.describe() if sc else None,
                "can": {"chat": user.has("operator"), "approve": user.has("approver"), "admin": user.has("admin")}}

    @app.get("/audit")
    def audit(limit: int = 100, user: User = Depends(require("approver"))):
        return store.list_audit(limit)

    @app.get("/users")
    def users_list(user: User = Depends(require("admin"))):
        return state.users.list()

    ui_built = (UI_DIST / "index.html").exists()

    @app.get("/")
    def index():
        """React 빌드(ui/dist)가 있으면 /app 으로, 없으면 내장 데모 페이지."""
        if ui_built:
            return RedirectResponse("/app/")
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))

    @app.get("/demo", response_class=HTMLResponse)
    def demo():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/tools")
    def tools(user: User = Depends(current_user)):
        return [{"name": t.name, "tier": str(t.tier), "policy": describe(policy.decide(t)), "description": t.description,
                 "label": pk.tool_labels.get(t.name, t.name)} for t in state.registry_for(user)]

    @app.post("/sessions")
    def create_session(body: SessionCreate, user: User = Depends(require("operator"))):
        return store.create_session(body.provider or state.default_provider, body.title)

    @app.get("/sessions/{sid}")
    def get_session(sid: str, user: User = Depends(current_user)):
        s = store.get_session(sid)
        if not s:
            raise HTTPException(404, "세션 없음")
        return s

    @app.post("/chat")
    async def chat(body: ChatRequest, request: Request, user: User = Depends(require("operator"))):
        sid = _session(body.session_id, body.provider)
        store.add_audit(user.name, user.role, "chat", sid, {"message": body.message[:200], "scope": user.scope}, request.client.host if request.client else None)
        return StreamingResponse(stream_chat(state, sid, body.message, body.provider, user=user), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/approvals")
    def list_approvals(status: str | None = Query(default="pending"), user: User = Depends(current_user)):
        return store.list_approvals(None if status in (None, "all") else status)

    @app.post("/approvals/{aid}")
    def decide(aid: str, body: ApprovalDecision, request: Request, user: User = Depends(require("approver"))):
        a = store.get_approval(aid)
        if not a:
            raise HTTPException(404, "승인 요청 없음")
        if not state.broker.decide(aid, body.decision == "approve", user.name):
            raise HTTPException(409, f"이미 처리됨: {a['status']}")
        store.add_audit(user.name, user.role, f"approval.{body.decision}", aid, {"tool": a["tool"], "args": a["args"], "run_id": a["run_id"]},
                        request.client.host if request.client else None)
        return store.get_approval(aid)

    @app.post("/jobs", status_code=202)
    def create_job(body: JobCreate, request: Request, user: User = Depends(require("operator"))):
        sid = _session(body.session_id, body.provider)
        run_id = state.jobs.submit(sid, body.question, body.provider, user=user)
        store.add_audit(user.name, user.role, "job.submit", run_id, {"question": body.question[:200], "scope": user.scope}, request.client.host if request.client else None)
        return {"run_id": run_id, "session_id": sid, "status": "queued"}

    @app.get("/jobs")
    def list_jobs(limit: int = 50, user: User = Depends(current_user)):
        return store.list_runs(limit, kind="job")

    @app.get("/jobs/{run_id}")
    def get_job(run_id: str, user: User = Depends(current_user)):
        r = store.get_run(run_id)
        if not r or r["kind"] != "job":
            raise HTTPException(404, "잡 없음")
        return r

    @app.post("/jobs/{run_id}/cancel")
    def cancel_job(run_id: str, user: User = Depends(require("operator"))):
        if not state.jobs.cancel(run_id):
            raise HTTPException(409, "취소할 수 없는 상태")
        store.add_audit(user.name, user.role, "job.cancel", run_id)
        return store.get_run(run_id)

    @app.get("/runs")
    def list_runs(limit: int = 50, kind: str | None = None, user: User = Depends(current_user)):
        return store.list_runs(limit, kind)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, user: User = Depends(current_user)):
        r = store.get_run(run_id)
        if not r:
            raise HTTPException(404, "실행 없음")
        r["events"] = store.events(run_id)
        r["approvals"] = [a for a in store.list_approvals(None, 200) if a["run_id"] == run_id]
        return r

    @app.get("/runs/{run_id}/trace")
    def run_trace(run_id: str, user: User = Depends(current_user)):
        """저장된 이벤트로 스팬(run/llm/tool/approval)을 재구성한다."""
        from ..observability.tracer import spans_from_events
        r = store.get_run(run_id)
        if not r:
            raise HTTPException(404, "실행 없음")
        c = spans_from_events(run_id, r["question"] or "", store.events(run_id), created_at=r.get("started_at") or r.get("created_at"))
        return {"summary": c.summary(), "spans": c.to_dicts()}

    RESULTS_DIR = Path(__file__).resolve().parents[1] / "evals" / "results"

    @app.get("/grading/{name}", response_class=HTMLResponse)
    def grading_page_route(name: str):
        """사람 채점표(결과 폴더의 grading/<name>.html). 평가 도구라 인증 없이 연다(업무 데이터 없음)."""
        if "/" in name or ".." in name:
            raise HTTPException(400, "잘못된 이름")
        f = RESULTS_DIR / "grading" / f"{name.removesuffix('.html')}.html"
        if not f.exists():
            raise HTTPException(404, f"채점표 없음: uv run harness grade-sheet 로 만드세요 ({f.name})")
        return f.read_text(encoding="utf-8")

    @app.post("/grading/{name}")
    async def grading_save(name: str, request: Request):
        if "/" in name or ".." in name:
            raise HTTPException(400, "잘못된 이름")
        body = await request.json()
        grades = body.get("grades") or {}
        graded = sum(1 for v in grades.values() if isinstance(v, dict) and v.get("pass") in (True, False))
        out = RESULTS_DIR / f"human-{name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        out.write_text(_json.dumps({"results": name, "graded_at": body.get("graded_at"), "grades": grades}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(out.relative_to(RESULTS_DIR.parents[2])), "graded": graded}

    @app.get("/runs/{run_id}/events")
    def run_events(run_id: str, after_seq: int = 0, user: User = Depends(current_user)):
        if not store.get_run(run_id):
            raise HTTPException(404, "실행 없음")
        return store.events(run_id, after_seq)

    from .a2a import mount_a2a
    mount_a2a(app, state, pk, current_user, require)  # A2A 에이전트 카드 + JSON-RPC(message/send·stream, tasks/get·cancel)

    for key, folder in pk.files.items():  # 팩이 만든 파일(그래프 등). 결과 카드가 /files/<key>/... 로 연다
        Path(folder).mkdir(parents=True, exist_ok=True)
        app.mount(f"/files/{key}", StaticFiles(directory=str(folder)), name=f"files-{key}")
    if ui_built:
        app.mount("/app", StaticFiles(directory=str(UI_DIST), html=True), name="ui")
    return app


def __getattr__(name: str):
    """`uvicorn harness.service.app:app` 용. import 시점에 DB 를 요구하지 않도록 첫 접근 때 만든다(PEP 562)."""
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
