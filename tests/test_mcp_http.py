"""MCP over HTTP(/mcp): 웹 API 와 같은 키·역할·데이터 범위를 쓰고, 변경 도구는 같은 결재함을 거친다."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from harness.service.app import create_app
from harness.service.auth import UserStore

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(client: TestClient, method: str, params: dict | None = None, key: str | None = None, rid: int = 1):
    headers = {**ACCEPT, **({"Authorization": f"Bearer {key}"} if key else {})}
    r = client.post(
        "/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    )
    if r.status_code != 200:
        return r.status_code, r.json()
    body = r.text
    if r.headers.get("content-type", "").startswith("text/event-stream"):  # json_response 가 꺼진 경우 대비
        body = "".join(line[5:].strip() for line in body.splitlines() if line.startswith("data:"))
    return r.status_code, json.loads(body)


def init(client: TestClient, key: str | None = None):
    return rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        key,
    )


def tool_text(resp: dict) -> str:
    return "".join(c.get("text", "") for c in resp["result"]["content"] if c.get("type") == "text")


@pytest.fixture
def mcp_env(seeded_db, tmp_path):
    users = UserStore(path=tmp_path / "users.json", users={}, required=True)
    keys = {
        "ops": users.add("ops", "operator", scope={"region": "부산"}),
        "boss": users.add("boss", "approver"),
        "root": users.add("root", "admin"),
    }
    app = create_app(db_path=str(tmp_path / "svc.sqlite"), pack="enterprise", users=users, approval_timeout_s=20)
    with TestClient(app) as client:
        yield client, keys


def test_requires_key_and_lists_tools_without_destructive(mcp_env):
    client, keys = mcp_env
    status, body = init(client)
    assert status == 401 and "API 키" in body["error"]
    status, body = init(client, keys["ops"])
    assert status == 200 and body["result"]["serverInfo"]["name"]
    status, body = rpc(client, "tools/list", key=keys["ops"], rid=2)
    tools = {t["name"]: t for t in body["result"]["tools"]}
    assert "run_sql" in tools and "create_note" in tools
    assert "delete_order" not in tools  # destructive 는 노출하지 않는다
    assert tools["run_sql"]["annotations"]["readOnlyHint"] is True
    assert tools["create_note"]["annotations"]["readOnlyHint"] is False
    assert "결재함" in tools["create_note"]["description"]
    assert client.get("/health").json()["mcp"]["url"] == "/mcp/"


def test_scope_applies_to_mcp_calls(mcp_env):
    client, keys = mcp_env
    sql = "SELECT COUNT(*) AS n FROM customers"
    _, scoped = rpc(client, "tools/call", {"name": "run_sql", "arguments": {"sql": sql}}, keys["ops"], rid=3)
    _, full = rpc(client, "tools/call", {"name": "run_sql", "arguments": {"sql": sql}}, keys["root"], rid=4)
    n_scoped = json.loads(tool_text(scoped))["rows"][0]["n"]
    n_full = json.loads(tool_text(full))["rows"][0]["n"]
    assert 0 < n_scoped < n_full  # 부산 담당자는 부산 거래처만
    audit = client.get("/audit", headers={"Authorization": f"Bearer {keys['boss']}"}).json()
    assert any(a["action"] == "mcp_call" and a["user"] == "ops" and a["target"] == "run_sql" for a in audit)


def _approve_when_pending(client, key, decision: str, found: dict):
    for _ in range(100):
        pend = client.get("/approvals?status=pending", headers={"Authorization": f"Bearer {key}"}).json()
        if pend:
            found["id"] = pend[0]["id"]
            client.post(
                f"/approvals/{pend[0]['id']}",
                headers={"Authorization": f"Bearer {key}"},
                json={"decision": decision, "by": "boss"},
            )
            return
        time.sleep(0.05)


def _scoped_order(client, key: str) -> int:
    """부산 담당자 범위 안의 주문 하나. 범위 밖 주문은 결재가 나도 도구가 거절한다(테스트에서 실제로 그랬다)."""
    body = rpc(
        client,
        "tools/call",
        {"name": "run_sql", "arguments": {"sql": "SELECT id FROM orders ORDER BY id LIMIT 1"}},
        key,
        rid=99,
    )[1]
    return json.loads(tool_text(body))["rows"][0]["id"]


def test_write_tool_waits_for_inbox_approval(mcp_env):
    client, keys = mcp_env
    oid = _scoped_order(client, keys["ops"])
    out: dict = {}
    found: dict = {}
    t = threading.Thread(
        target=lambda: out.update(
            dict(
                zip(
                    ("status", "body"),
                    rpc(
                        client,
                        "tools/call",
                        {
                            "name": "create_note",
                            "arguments": {"order_id": oid, "note": "MCP 에서 남김", "author": "ops"},
                        },
                        keys["ops"],
                        rid=5,
                    ),
                )
            )
        )
    )
    t.start()
    _approve_when_pending(client, keys["boss"], "approve", found)
    t.join(timeout=20)
    assert "id" in found and out["status"] == 200
    res = json.loads(tool_text(out["body"]))
    assert res["approved"] is True and res["approval_id"] == found["id"] and res["result"]["order_id"] == oid
    run = client.get(f"/runs/{res['run_id']}", headers={"Authorization": f"Bearer {keys['ops']}"}).json()
    assert run["kind"] == "mcp" and run["status"] == "done"
    kinds = [e["kind"] for e in run["events"]]
    assert kinds == ["tool_call", "approval_pending", "approval_result", "tool_result", "final"]
    assert run["approvals"][0]["status"] == "approved" and run["approvals"][0]["decided_by"] == "boss"


def test_write_tool_denied_does_not_execute(mcp_env):
    client, keys = mcp_env
    oid = _scoped_order(client, keys["ops"])
    before = client.get("/runs?limit=5", headers={"Authorization": f"Bearer {keys['ops']}"}).json()
    out: dict = {}
    found: dict = {}
    t = threading.Thread(
        target=lambda: out.update(
            dict(
                zip(
                    ("status", "body"),
                    rpc(
                        client,
                        "tools/call",
                        {"name": "create_note", "arguments": {"order_id": oid, "note": "반려될 메모", "author": "ops"}},
                        keys["ops"],
                        rid=6,
                    ),
                )
            )
        )
    )
    t.start()
    _approve_when_pending(client, keys["boss"], "deny", found)
    t.join(timeout=20)
    res = json.loads(tool_text(out["body"]))
    assert res["approved"] is False and "실행하지 않았습니다" in res["note"]
    notes = rpc(
        client,
        "tools/call",
        {"name": "run_sql", "arguments": {"sql": "SELECT COUNT(*) AS n FROM order_notes WHERE note='반려될 메모'"}},
        keys["root"],
        rid=7,
    )[1]
    assert json.loads(tool_text(notes))["rows"][0]["n"] == 0
    assert (
        len(client.get("/runs?limit=5", headers={"Authorization": f"Bearer {keys['ops']}"}).json()) == len(before) + 1
    )


def test_viewer_cannot_call_tools(mcp_env, tmp_path):
    client, keys = mcp_env
    users = UserStore(path=tmp_path / "users2.json", users={}, required=True)
    viewer = users.add("guest", "viewer")
    client.app.state.harness.users.users.update(users.users)  # 같은 앱에 viewer 추가
    status, body = rpc(
        client, "tools/call", {"name": "run_sql", "arguments": {"sql": "SELECT 1 AS one"}}, viewer, rid=8
    )
    assert status == 200 and body["result"].get("isError") is True and "operator" in tool_text(body)


def test_out_of_scope_write_is_refused_with_a_clear_message(mcp_env):
    client, keys = mcp_env
    ids = lambda key, rid: {  # noqa: E731
        r["id"]
        for r in json.loads(
            tool_text(
                rpc(
                    client,
                    "tools/call",
                    {"name": "run_sql", "arguments": {"sql": "SELECT id FROM orders"}},
                    key,
                    rid=rid,
                )[1]
            )
        )["rows"]
    }
    outside = min(ids(keys["root"], 20) - ids(keys["ops"], 21))  # 전체에는 있고 부산 담당자에게는 없는 주문
    status, body = rpc(
        client, "tools/call", {"name": "get_order", "arguments": {"order_id": outside}}, keys["ops"], rid=22
    )
    # 범위 밖 주문은 "없음" 으로 보인다(임시 뷰가 잘라서). 어느 쪽이든 실행되지 않고 이유가 글로 온다
    assert (
        status == 200
        and body["result"].get("isError") is True
        and ("범위" in tool_text(body) or "없음" in tool_text(body))
    )
