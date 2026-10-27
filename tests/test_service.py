"""FastAPI 서비스 테스트: 가짜 프로바이더로 SSE 스트림·승인·잡 큐를 결정적으로 검증한다."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from harness.agent.types import LLMResponse, ToolCall
from harness.packs.enterprise.tools import SYSTEM_PROMPT, build_tools
from harness.service.app import create_app
from harness.tools.registry import ToolRegistry


class ScriptProvider:
    name = "fake"
    model = "fake"

    def __init__(self, script, delay=0.0):
        self.script = list(script)
        self.delay = delay

    def complete(self, messages, tools=None):
        if self.delay:
            time.sleep(self.delay)
        return self.script.pop(0) if self.script else LLMResponse(text="끝")


def tc(name, **args):
    return ToolCall(id=f"c_{name}_{len(args)}", name=name, arguments=args)


def parse_sse(lines):
    events, ev, data = [], None, ""
    for line in lines:
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data += line[5:].strip()
        elif line == "" and ev:
            events.append((ev, json.loads(data) if data else {}))
            ev, data = None, ""
    return events


@pytest.fixture
def make_client(tmp_path, seeded_db):
    db, _ = seeded_db
    registry = ToolRegistry(build_tools(db))

    def _make(scripts, approval_timeout_s=5.0, delay=0.0):
        it = iter(scripts)
        app = create_app(db_path=tmp_path / "svc.db", provider_factory=lambda spec: ScriptProvider(next(it), delay),
                         registry=registry, system_prompt=SYSTEM_PROMPT, approval_timeout_s=approval_timeout_s)
        return TestClient(app)

    return _make


def test_health_and_tools(make_client):
    with make_client([]) as c:
        h = c.get("/health").json()
        assert h["status"] == "ok" and h["tools"] == 10
        tools = c.get("/tools").json()
        assert {t["name"]: t["policy"] for t in tools}["delete_order"] == "차단"


def test_chat_streams_events_and_persists_run(make_client):
    script = [LLMResponse(tool_calls=[tc("run_sql", sql="SELECT COUNT(*) AS n FROM orders WHERE status='지연'", limit=1)]),
              LLMResponse(text="지연 주문은 255건입니다.")]
    with make_client([script]) as c:
        with c.stream("POST", "/chat", json={"message": "지연 주문 몇 건?"}) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            events = parse_sse(r.iter_lines())
    kinds = [e for e, _ in events]
    assert kinds[0] == "run" and kinds[-1] == "done"
    assert "tool_call" in kinds and "tool_result" in kinds and "final" in kinds
    done = dict(events)["done"]
    assert done["answer"] == "지연 주문은 255건입니다." and done["stopped_reason"] == "final"
    run_id, sid = dict(events)["run"]["run_id"], dict(events)["run"]["session_id"]
    with c:
        run = c.get(f"/runs/{run_id}").json()
        assert run["status"] == "done" and len(run["events"]) >= 4
        assert [e["kind"] for e in run["events"]][0] == "step"
        sess = c.get(f"/sessions/{sid}").json()
        assert [m["role"] for m in sess["messages"]] == ["user", "assistant"]


def test_approval_flow_over_http(make_client, seeded_db):
    script = [LLMResponse(tool_calls=[tc("create_note", order_id=1, note="서비스 테스트 메모")]), LLMResponse(text="메모를 남겼습니다.")]
    with make_client([script]) as c:
        decided = {}

        def approve_when_pending(aid):
            decided["resp"] = c.post(f"/approvals/{aid}", json={"decision": "approve", "by": "test"})

        events = []
        with c.stream("POST", "/chat", json={"message": "주문 1번에 메모 남겨줘"}) as r:
            ev, data = None, ""
            for line in r.iter_lines():
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data += line[5:].strip()
                elif line == "" and ev:
                    d = json.loads(data) if data else {}
                    events.append((ev, d))
                    if ev == "approval_pending":
                        threading.Thread(target=approve_when_pending, args=(d["approval_id"],), daemon=True).start()
                    ev, data = None, ""
        kinds = [e for e, _ in events]
        assert "approval_pending" in kinds and "approval_result" in kinds
        assert dict(events)["approval_result"]["approved"] is True
        assert dict(events)["done"]["answer"] == "메모를 남겼습니다."
        assert decided["resp"].status_code == 200 and decided["resp"].json()["status"] == "approved"
        assert c.get("/approvals?status=pending").json() == []
        # 두 번 결정하면 409
        aid = decided["resp"].json()["id"]
        assert c.post(f"/approvals/{aid}", json={"decision": "deny"}).status_code == 409
    # 실제로 메모가 DB 에 들어갔는지
    registry = ToolRegistry(build_tools(seeded_db[0]))
    notes = registry.get("get_order").run({"order_id": 1})["notes"]
    assert any(n["note"] == "서비스 테스트 메모" for n in notes)


def test_approval_expires_without_decision(make_client):
    script = [LLMResponse(tool_calls=[tc("update_order_status", order_id=2, new_status="출고", reason="테스트")]), LLMResponse(text="승인이 없어 변경하지 않았습니다.")]
    with make_client([script], approval_timeout_s=0.5) as c:
        with c.stream("POST", "/chat", json={"message": "주문 2번 출고로"}) as r:
            events = dict(parse_sse(r.iter_lines()))
        assert events["approval_result"]["approved"] is False
        assert events["done"]["answer"].startswith("승인이 없어")
        assert c.get("/approvals?status=expired").json()[0]["tool"] == "update_order_status"


def test_jobs_submit_poll_and_cancel(make_client):
    quick = [LLMResponse(text="잡 결과입니다.")]
    slow = [LLMResponse(tool_calls=[tc("list_tables")]) for _ in range(8)] + [LLMResponse(text="끝")]
    with make_client([quick, slow], delay=0.0) as c:
        r = c.post("/jobs", json={"question": "빠른 잡"})
        assert r.status_code == 202
        rid = r.json()["run_id"]
        for _ in range(50):
            j = c.get(f"/jobs/{rid}").json()
            if j["status"] == "done":
                break
            time.sleep(0.1)
        assert j["status"] == "done" and j["answer"] == "잡 결과입니다."
        assert c.get("/jobs").json()[0]["id"] == rid
        # 취소: 아직 큐에 있거나 실행 중일 때
        rid2 = c.post("/jobs", json={"question": "느린 잡"}).json()["run_id"]
        resp = c.post(f"/jobs/{rid2}/cancel")
        assert resp.status_code in (200, 409)
        for _ in range(50):
            j2 = c.get(f"/jobs/{rid2}").json()
            if j2["status"] in ("done", "cancelled", "failed"):
                break
            time.sleep(0.1)
        assert j2["status"] in ("cancelled", "done")
        assert c.get("/runs?kind=job").json()[0]["id"] == rid2


def test_run_trace_endpoint(make_client):
    script = [LLMResponse(tool_calls=[tc("list_tables")]), LLMResponse(text="끝")]
    with make_client([script]) as c:
        with c.stream("POST", "/chat", json={"message": "테이블?"}) as r:
            run_id = dict(parse_sse(r.iter_lines()))["run"]["run_id"]
        t = c.get(f"/runs/{run_id}/trace").json()
        kinds = [s["kind"] for s in t["spans"]]
        assert kinds[0] == "run" and "tool" in kinds and "llm" in kinds
        assert t["summary"]["tool_calls"] == 1 and all(s["end"] is not None for s in t["spans"])


def test_stale_runs_are_failed_on_startup(tmp_path, seeded_db):
    from harness.service.store import Store
    db = tmp_path / "stale.db"
    s = Store(db)
    sid = s.create_session("fake")["id"]
    r1 = s.create_run(sid, "chat", "q"); s.start_run(r1)
    r2 = s.create_run(sid, "job", "q2")
    aid = s.create_approval(r1, "create_note", {"order_id": 1}, "write")
    assert s.fail_stale() == 2
    assert s.get_run(r1)["status"] == "failed" and s.get_run(r2)["status"] == "failed"
    assert s.get_approval(aid)["status"] == "expired"
    assert s.list_approvals("pending") == []


def test_health_reports_model_server_status(tmp_path, seeded_db):
    db, _ = seeded_db
    from harness.service.auth import UserStore
    state = {"ok": False}
    app = create_app(db_path=tmp_path / "svc.db", registry=ToolRegistry(build_tools(db)), system_prompt=SYSTEM_PROMPT,
                     users=UserStore(path=tmp_path / "none.json"), model_probe=lambda: state["ok"], default_provider="local")
    with TestClient(app) as c:
        m = c.get("/health").json()["model"]
        assert m == {"provider": "local", "reachable": False, "hosted": False}
