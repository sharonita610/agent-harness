"""FastAPI 앱. uv run uvicorn harness.service.app:app --port 8787"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse

from ..agent.providers import make_provider
from ..tools.permissions import Policy, describe
from ..tools.registry import ToolRegistry
from .broker import ApprovalBroker
from .jobs import JobQueue
from .models import ApprovalDecision, ChatRequest, JobCreate, SessionCreate
from .runner import stream_chat
from .store import Store

STATIC = Path(__file__).parent / "static"
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


def create_app(db_path: str | Path | None = None, provider_factory: Callable[[str], Any] | None = None,
               registry: ToolRegistry | None = None, system_prompt: str | None = None,
               approval_timeout_s: float = 600, default_provider: str | None = None, job_concurrency: int = 1) -> FastAPI:
    if registry is None or system_prompt is None:
        from ..packs.enterprise.tools import SYSTEM_PROMPT, build_tools
        registry = registry or ToolRegistry(build_tools())
        system_prompt = system_prompt or SYSTEM_PROMPT
    store = Store(db_path or os.getenv("HARNESS_SERVICE_DB", str(DEFAULT_DB)))
    state = AppState(store=store, broker=ApprovalBroker(store, approval_timeout_s), registry=registry, system_prompt=system_prompt,
                     provider_factory=provider_factory or make_provider,
                     default_provider=default_provider or os.getenv("HARNESS_PROVIDER", "local"))
    state.jobs = JobQueue(state, concurrency=job_concurrency)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
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
        return {"status": "ok", "tools": len(registry), "provider": state.default_provider, "pending_approvals": len(state.broker.pending())}

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/tools")
    def tools():
        return [{"name": t.name, "tier": str(t.tier), "policy": describe(policy.decide(t)), "description": t.description} for t in registry]

    @app.post("/sessions")
    def create_session(body: SessionCreate):
        return store.create_session(body.provider or state.default_provider, body.title)

    @app.get("/sessions/{sid}")
    def get_session(sid: str):
        s = store.get_session(sid)
        if not s:
            raise HTTPException(404, "세션 없음")
        return s

    @app.post("/chat")
    async def chat(body: ChatRequest):
        sid = _session(body.session_id, body.provider)
        return StreamingResponse(stream_chat(state, sid, body.message, body.provider), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/approvals")
    def list_approvals(status: str | None = Query(default="pending")):
        return store.list_approvals(None if status in (None, "all") else status)

    @app.post("/approvals/{aid}")
    def decide(aid: str, body: ApprovalDecision):
        a = store.get_approval(aid)
        if not a:
            raise HTTPException(404, "승인 요청 없음")
        if not state.broker.decide(aid, body.decision == "approve", body.by):
            raise HTTPException(409, f"이미 처리됨: {a['status']}")
        return store.get_approval(aid)

    @app.post("/jobs", status_code=202)
    def create_job(body: JobCreate):
        sid = _session(body.session_id, body.provider)
        run_id = state.jobs.submit(sid, body.question, body.provider)
        return {"run_id": run_id, "session_id": sid, "status": "queued"}

    @app.get("/jobs")
    def list_jobs(limit: int = 50):
        return store.list_runs(limit, kind="job")

    @app.get("/jobs/{run_id}")
    def get_job(run_id: str):
        r = store.get_run(run_id)
        if not r or r["kind"] != "job":
            raise HTTPException(404, "잡 없음")
        return r

    @app.post("/jobs/{run_id}/cancel")
    def cancel_job(run_id: str):
        if not state.jobs.cancel(run_id):
            raise HTTPException(409, "취소할 수 없는 상태")
        return store.get_run(run_id)

    @app.get("/runs")
    def list_runs(limit: int = 50, kind: str | None = None):
        return store.list_runs(limit, kind)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        r = store.get_run(run_id)
        if not r:
            raise HTTPException(404, "실행 없음")
        r["events"] = store.events(run_id)
        r["approvals"] = [a for a in store.list_approvals(None, 200) if a["run_id"] == run_id]
        return r

    @app.get("/runs/{run_id}/trace")
    def run_trace(run_id: str):
        """저장된 이벤트로 스팬(run/llm/tool/approval)을 재구성한다."""
        from ..observability.tracer import spans_from_events
        r = store.get_run(run_id)
        if not r:
            raise HTTPException(404, "실행 없음")
        c = spans_from_events(run_id, r["question"] or "", store.events(run_id), created_at=r.get("started_at") or r.get("created_at"))
        return {"summary": c.summary(), "spans": c.to_dicts()}

    @app.get("/runs/{run_id}/events")
    def run_events(run_id: str, after_seq: int = 0):
        if not store.get_run(run_id):
            raise HTTPException(404, "실행 없음")
        return store.events(run_id, after_seq)

    return app


def __getattr__(name: str):
    """`uvicorn harness.service.app:app` 용. import 시점에 DB 를 요구하지 않도록 첫 접근 때 만든다(PEP 562)."""
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
