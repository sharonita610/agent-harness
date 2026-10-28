"""A2A 서버 면(에이전트 카드·message/send·tasks/get·stream·결재→input-required)과 총괄 비서 도구(A2A 클라이언트)."""
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
    model = "fake"

    def __init__(self, script):
        self.script = list(script)

    def complete(self, messages, tools=None, **_):
        return self.script.pop(0) if self.script else LLMResponse(text="끝")


def _tc(name, **args):
    return ToolCall(id=f"call_{name}", name=name, arguments=args)


@pytest.fixture
def app(seeded_db, tmp_path):
    db, _ = seeded_db
    scripts = {
        "현재 상태가 '지연'인 주문은 총 몇 건이야?": [LLMResponse(tool_calls=[_tc("run_sql", sql="SELECT COUNT(*) AS n FROM orders WHERE status='지연'")]), LLMResponse(text="지연 주문은 255건입니다.")],
        "주문 1번에 메모 남겨줘": [LLMResponse(tool_calls=[_tc("create_note", order_id=1, author="a2a", note="확인")]), LLMResponse(text="메모를 남겼습니다.")],
    }

    def factory(spec):
        return _Router(scripts)
    return create_app(db_path=tmp_path / "svc.db", provider_factory=factory, registry=ToolRegistry(build_tools(db)), system_prompt=SYSTEM_PROMPT, approval_timeout_s=5)


class _Router:
    """질문 텍스트로 스크립트를 고르는 가짜 프로바이더."""
    model = "fake"

    def __init__(self, scripts):
        self.scripts = scripts
        self.current = None

    def complete(self, messages, tools=None, **_):
        if self.current is None:
            q = next(m["content"] for m in reversed(messages) if m["role"] == "user")
            self.current = ScriptProvider(self.scripts.get(q, [LLMResponse(text="모르겠습니다")]))
        return self.current.complete(messages, tools)


def _send(c, text, blocking=True, context_id=None):
    msg = {"kind": "message", "role": "user", "messageId": "m1", "parts": [{"kind": "text", "text": text}]}
    if context_id:
        msg["contextId"] = context_id
    r = c.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": msg, "configuration": {"blocking": blocking, "timeoutSeconds": 10}}})
    return r.json()


def test_agent_card_and_errors(app):
    with TestClient(app) as c:
        card = c.get("/.well-known/agent-card.json").json()
        assert card["name"] == "한빛상사 비서" and card["url"].endswith("/a2a") and card["capabilities"]["streaming"] is True
        assert card["metadata"]["pack"] == "enterprise" and any(s["id"] == "tools" for s in card["skills"]) and card["protocolVersion"].startswith("0.")
        assert c.get("/.well-known/agent.json").json()["name"] == card["name"]
        assert c.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {"id": "nope"}}).json()["error"]["code"] == -32001
        assert c.post("/a2a", json={"jsonrpc": "2.0", "id": 2, "method": "nope"}).json()["error"]["code"] == -32601
        assert c.post("/a2a", json={"jsonrpc": "2.0", "id": 3, "method": "message/send", "params": {"message": {"parts": []}}}).json()["error"]["code"] == -32602


def test_send_blocking_completes_with_artifact(app):
    with TestClient(app) as c:
        body = _send(c, "현재 상태가 '지연'인 주문은 총 몇 건이야?")
        task = body["result"]
        assert task["kind"] == "task" and task["status"]["state"] == "completed"
        assert task["artifacts"][0]["parts"][0]["text"] == "지연 주문은 255건입니다." and task["history"][-1]["role"] == "agent"
        got = c.post("/a2a", json={"jsonrpc": "2.0", "id": 5, "method": "tasks/get", "params": {"id": task["id"], "historyLength": 1}}).json()["result"]
        assert got["status"]["state"] == "completed" and len(got["history"]) == 1 and got["contextId"] == task["contextId"]
        # 같은 contextId 로 보내면 같은 세션
        again = _send(c, "현재 상태가 '지연'인 주문은 총 몇 건이야?", context_id=task["contextId"])["result"]
        assert again["contextId"] == task["contextId"] and again["id"] != task["id"]


def test_approval_becomes_input_required_then_completes(app):
    with TestClient(app) as c:
        task = _send(c, "주문 1번에 메모 남겨줘")["result"]
        assert task["status"]["state"] == "input-required" and "create_note" in task["status"]["message"]["parts"][0]["text"]
        aid = c.get("/approvals").json()[0]["id"]
        c.post(f"/approvals/{aid}", json={"decision": "approve", "by": "테스트"})
        for _ in range(50):
            got = c.post("/a2a", json={"jsonrpc": "2.0", "id": 9, "method": "tasks/get", "params": {"id": task["id"]}}).json()["result"]
            if got["status"]["state"] == "completed":
                break
            time.sleep(0.1)
        assert got["status"]["state"] == "completed" and "메모를 남겼습니다" in got["artifacts"][0]["parts"][0]["text"]


def test_stream_emits_status_and_artifact(app):
    with TestClient(app) as c:
        msg = {"kind": "message", "role": "user", "messageId": "m2", "parts": [{"kind": "text", "text": "현재 상태가 '지연'인 주문은 총 몇 건이야?"}]}
        with c.stream("POST", "/a2a", json={"jsonrpc": "2.0", "id": 7, "method": "message/stream", "params": {"message": msg, "configuration": {"blocking": False}}}) as r:
            kinds, arts = [], []
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                res = json.loads(line[6:])["result"]
                kinds.append(res.get("kind"))
                if res.get("kind") == "artifact-update":
                    arts.append(res["artifact"]["parts"][0]["text"])
        assert kinds[0] == "task" and "status-update" in kinds and arts == ["지연 주문은 255건입니다."]


def test_supervisor_tools_delegate_over_a2a(app):
    from harness.packs.supervisor.tools import build_tools as build_sup
    with TestClient(app) as c:
        reg = ToolRegistry(build_sup(agents=["http://testserver"], http=c, index=None))
        agents = reg.get("list_agents").run({})["agents"]
        assert agents[0]["id"] == "enterprise" and agents[0]["status"] == "ok"
        out = reg.get("delegate").run({"agent": "회사", "question": "현재 상태가 '지연'인 주문은 총 몇 건이야?"})
        assert out["agent"] == "enterprise" and out["state"] == "completed" and "255" in out["answer"]
        wait = reg.get("delegate").run({"agent": "enterprise", "question": "주문 1번에 메모 남겨줘"})
        assert wait["state"] == "input-required" and "결재" in wait["note"]
        chk = reg.get("check_task").run({"agent": "enterprise", "task_id": wait["task_id"]})
        assert chk["state"] in ("input-required", "working")
        with pytest.raises(ValueError):
            reg.get("delegate").run({"agent": "없는비서", "question": "x"})


def test_expect_calls_scoring():
    from harness.agent.types import AgentResult
    from harness.evals.runner import score
    task = {"id": "r", "type": "route", "question": "q", "expect_calls": [{"name": "delegate", "args_contains": {"agent": "spice"}}]}
    ok = AgentResult(answer="a", steps=2, stopped_reason="final", tool_calls=[{"name": "delegate", "status": "ok", "arguments": {"agent": "spice", "question": "x"}}])
    bad = AgentResult(answer="a", steps=2, stopped_reason="final", tool_calls=[{"name": "delegate", "status": "ok", "arguments": {"agent": "enterprise", "question": "x"}}])
    assert score(task, ok, [])["pass"] and not score(task, bad, [])["pass"]
