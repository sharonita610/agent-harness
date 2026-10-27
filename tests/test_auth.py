"""인증·인가·감사: 키 없음 401, 역할 부족 403, 결재자만 승인, 감사 기록에 사용자 이름."""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from harness.agent.types import LLMResponse, ToolCall
from harness.packs.enterprise.tools import SYSTEM_PROMPT, build_tools
from harness.service.app import create_app
from harness.service.auth import UserStore
from harness.tools.registry import ToolRegistry
from tests.test_service import ScriptProvider, parse_sse


def tc(name, **args):
    return ToolCall(id=f"c_{name}", name=name, arguments=args)


@pytest.fixture
def auth_client(tmp_path, seeded_db):
    db, _ = seeded_db
    us = UserStore(path=tmp_path / "users.json")
    k_op = us.add("운영자", "operator")
    k_ap = us.add("팀장", "approver")
    k_ad = us.add("관리자", "admin")
    scripts = [[LLMResponse(tool_calls=[tc("create_note", order_id=3, note="인증 테스트")]), LLMResponse(text="남겼습니다.")]]
    it = iter(scripts)
    app = create_app(db_path=tmp_path / "svc.db", provider_factory=lambda spec: ScriptProvider(next(it)), registry=ToolRegistry(build_tools(db)),
                     system_prompt=SYSTEM_PROMPT, approval_timeout_s=5, users=us)
    return TestClient(app), {"op": k_op, "ap": k_ap, "ad": k_ad}


def H(key):
    return {"Authorization": f"Bearer {key}"}


def test_missing_or_wrong_key_is_401(auth_client):
    c, keys = auth_client
    with c:
        assert c.get("/health").status_code == 200  # 헬스는 공개
        assert c.get("/health").json()["auth_required"] is True
        assert c.get("/runs").status_code == 401
        assert c.get("/runs", headers=H("hk_wrong")).status_code == 401
        assert c.post("/chat", json={"message": "x"}).status_code == 401


def test_me_and_role_flags(auth_client):
    c, keys = auth_client
    with c:
        me = c.get("/me", headers=H(keys["op"])).json()
        assert me["name"] == "운영자" and me["can"] == {"chat": True, "approve": False, "admin": False}
        assert c.get("/me", headers=H(keys["ap"])).json()["can"]["approve"] is True


def test_operator_cannot_approve_but_approver_can_and_audit_records_it(auth_client, seeded_db):
    c, keys = auth_client
    with c:
        events = []
        result = {}

        def try_decide(aid):
            result["op"] = c.post(f"/approvals/{aid}", json={"decision": "approve"}, headers=H(keys["op"]))
            result["ap"] = c.post(f"/approvals/{aid}", json={"decision": "approve"}, headers=H(keys["ap"]))

        with c.stream("POST", "/chat", json={"message": "주문 3번에 메모"}, headers=H(keys["op"])) as r:
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
                        threading.Thread(target=try_decide, args=(d["approval_id"],), daemon=True).start()
                    ev, data = None, ""
        assert result["op"].status_code == 403 and "approver" in result["op"].json()["detail"]
        assert result["ap"].status_code == 200 and result["ap"].json()["decided_by"] == "팀장"
        assert dict(events)["approval_result"]["approved"] is True
        audit = c.get("/audit", headers=H(keys["ap"])).json()
        actions = [(a["user"], a["action"]) for a in audit]
        assert ("팀장", "approval.approve") in actions and ("운영자", "chat") in actions
        assert c.get("/audit", headers=H(keys["op"])).status_code == 403
        assert c.get("/users", headers=H(keys["ap"])).status_code == 403
        assert [u["name"] for u in c.get("/users", headers=H(keys["ad"])).json()] == ["운영자", "팀장", "관리자"]


def test_user_store_hashes_keys_and_persists(tmp_path):
    us = UserStore(path=tmp_path / "u.json")
    key = us.add("a", "viewer")
    raw = (tmp_path / "u.json").read_text()
    assert key not in raw and UserStore.hash_key(key) in raw
    again = UserStore.load(tmp_path / "u.json")
    assert again.required and again.authenticate(key).name == "a" and again.authenticate("nope") is None
    assert us.remove("a") == 1 and UserStore.load(tmp_path / "u.json").authenticate(key) is None


def test_dev_mode_without_users_is_open(tmp_path, seeded_db):
    db, _ = seeded_db
    us = UserStore(path=tmp_path / "none.json")  # 사용자 없음 → required False
    app = create_app(db_path=tmp_path / "svc.db", registry=ToolRegistry(build_tools(db)), system_prompt=SYSTEM_PROMPT, users=us)
    with TestClient(app) as c:
        me = c.get("/me").json()
        assert me["anonymous"] is True and me["role"] == "admin" and me["auth_required"] is False
