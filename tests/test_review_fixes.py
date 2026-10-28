"""2026-10-29 코드 검토에서 나온 결함들의 회귀 테스트: describe_table, 저장 벡터 주입, provider 허용 목록, 실행 소유자, 결재 대기 중 취소, A2A 취소 권한."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from harness.agent.types import LLMResponse, ToolCall
from harness.packs.spice.netlist import NetlistError
from harness.packs.spice.simulator import ngspice_available, simulate
from harness.service.app import create_app
from harness.service.auth import UserStore
from harness.tools.registry import ToolRegistry


def test_describe_table_works_on_readonly_connections(seeded_db, factory_db):
    """읽기 전용 연결의 권한 설정이 PRAGMA 를 막아 항상 실패하던 도구. 두 팩 모두 pragma_table_info 로 통과해야 한다."""
    from harness.packs.enterprise.tools import build_tools as ent
    from harness.packs.factory.tools import build_tools as fac

    db, _ = seeded_db
    r = ToolRegistry(ent(db_path=db, index=None)).get("describe_table").run({"table": "orders"})
    assert {c["name"] for c in r["columns"]} >= {"id", "customer_id", "status"} and len(r["sample_rows"]) == 3
    fdb, _ = factory_db
    r = ToolRegistry(fac(db_path=fdb, index=None)).get("describe_table").run({"table": "inspections"})
    assert {c["name"] for c in r["columns"]} >= {"hour_start", "inspected", "ng"}


@pytest.mark.skipif(not ngspice_available(), reason="ngspice 미설치")
def test_save_vectors_cannot_inject_control_commands(tmp_path):
    net = "* rc\nV1 in 0 dc 0 ac 1\nR1 in out 1k\nC1 out 0 100n\n.ac dec 10 10 1meg\n.end"
    with pytest.raises(NetlistError, match="저장 벡터 형식"):
        simulate(net, ["fc WHEN vdb(out)=-3"], save_vectors=["v(out)\nshell ls"], runs_dir=tmp_path)
    with pytest.raises(NetlistError, match="저장 벡터 형식"):
        simulate(net, ["fc WHEN vdb(out)=-3"], save_vectors=["v(out); echo hi"], runs_dir=tmp_path)
    ok = simulate(net, ["fc WHEN vdb(out)=-3"], save_vectors=["v(out)"], runs_dir=tmp_path)
    assert ok["measurements"]["fc"] > 1000


class Scripted:
    def __init__(self, script):
        self.script, self.i = script, 0

    def complete(self, messages, tools=None):
        r = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return r


def _app(tmp_path, script, users=None, **kw):
    return create_app(
        db_path=str(tmp_path / "svc.sqlite"),
        pack="enterprise",
        users=users,
        provider_factory=lambda spec: Scripted(script),
        **kw,
    )


def _users(tmp_path):
    us = UserStore(path=tmp_path / "users.json", users={}, required=True)
    keys = {
        "a": us.add("ops-a", "operator"),
        "b": us.add("ops-b", "operator"),
        "boss": us.add("boss", "approver"),
        "v": us.add("guest", "viewer"),
    }
    return us, keys


def H(key):
    return {"Authorization": f"Bearer {key}"}


def test_provider_from_request_must_be_allowed(tmp_path):
    client = TestClient(
        _app(tmp_path, [LLMResponse(text="답", tool_calls=[], usage={"prompt_tokens": 1, "completion_tokens": 1})])
    )
    bad = client.post("/chat", json={"message": "q", "provider": "compat:http://evil.example/v1|m"})
    assert bad.status_code == 400 and "허용되지 않은 provider" in bad.json()["detail"]
    assert client.post("/jobs", json={"question": "q", "provider": "openai:gpt-5"}).status_code == 400
    assert client.post("/sessions", json={"provider": "anthropic:x"}).status_code == 400
    ok = client.post("/sessions", json={"provider": "local"})  # 기본 provider 는 허용
    assert ok.status_code == 200


def test_runs_and_sessions_are_visible_only_to_owner_or_approver(tmp_path):
    us, k = _users(tmp_path)
    client = TestClient(
        _app(
            tmp_path,
            [LLMResponse(text="답", tool_calls=[], usage={"prompt_tokens": 1, "completion_tokens": 1})],
            users=us,
        )
    )
    r = client.post("/chat", json={"message": "a 의 질문"}, headers=H(k["a"]))
    assert r.status_code == 200
    run_id = json.loads([ln for ln in r.text.splitlines() if ln.startswith("data:")][0][5:])["run_id"]
    sid = json.loads([ln for ln in r.text.splitlines() if ln.startswith("data:")][0][5:])["session_id"]
    assert run_id in {x["id"] for x in client.get("/runs", headers=H(k["a"])).json()}
    assert run_id not in {x["id"] for x in client.get("/runs", headers=H(k["b"])).json()}
    assert client.get(f"/runs/{run_id}", headers=H(k["b"])).status_code == 404
    assert client.get(f"/runs/{run_id}/events", headers=H(k["b"])).status_code == 404
    assert client.get(f"/runs/{run_id}", headers=H(k["boss"])).status_code == 200  # 결재자는 전부
    assert client.get(f"/sessions/{sid}", headers=H(k["b"])).status_code == 404
    assert client.post("/chat", json={"message": "남의 세션", "session_id": sid}, headers=H(k["b"])).status_code == 403


def test_cancelling_a_job_closes_its_pending_approval(tmp_path):
    us, k = _users(tmp_path)
    script = [
        LLMResponse(
            text=None,
            tool_calls=[
                ToolCall(
                    id="c1", name="create_note", arguments={"order_id": 1, "note": "검토 테스트 메모", "author": "t"}
                )
            ],
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        ),
        LLMResponse(text="끝", tool_calls=[], usage={"prompt_tokens": 1, "completion_tokens": 1}),
    ]
    app = _app(tmp_path, script, users=us, approval_timeout_s=30)
    with TestClient(app) as client:
        job = client.post("/jobs", json={"question": "메모"}, headers=H(k["a"])).json()["run_id"]
        for _ in range(100):
            pend = client.get("/approvals?status=pending", headers=H(k["boss"])).json()
            if pend:
                break
            time.sleep(0.05)
        assert pend and pend[0]["run_id"] == job
        assert client.post(f"/jobs/{job}/cancel", headers=H(k["a"])).status_code == 200
        for _ in range(100):
            run = client.get(f"/runs/{job}", headers=H(k["a"])).json()
            if run["status"] in ("cancelled", "done", "failed"):
                break
            time.sleep(0.05)
        assert run["status"] == "cancelled"
        aid = pend[0]["id"]
        allp = {a["id"]: a for a in client.get("/approvals?status=all", headers=H(k["boss"])).json()}
        assert allp[aid]["status"] == "cancelled"
        # 뒤늦게 승인해도 실행되지 않는다
        assert (
            client.post(
                f"/approvals/{aid}", json={"decision": "approve", "by": "boss"}, headers=H(k["boss"])
            ).status_code
            == 409
        )


def test_a2a_cancel_requires_operator(tmp_path):
    us, k = _users(tmp_path)
    client = TestClient(
        _app(
            tmp_path,
            [LLMResponse(text="답", tool_calls=[], usage={"prompt_tokens": 1, "completion_tokens": 1})],
            users=us,
        )
    )
    r = client.post(
        "/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/cancel", "params": {"id": "run_x"}}, headers=H(k["v"])
    )
    assert r.status_code == 403 and "operator" in r.json()["error"]["message"]
