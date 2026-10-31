"""총괄 비서의 동시 위임(delegate_many, ADR-032): 가짜 하위 비서 둘로 (1) 동시 실행이 순차보다 빠른 것, (2) 한쪽이 결재 대기
(input-required)일 때의 합치기, (3) 같은 비서로 가는 항목은 순차(contextId 이어 쓰기)이고 한 항목이 실패해도 부분 결과가 남는 것."""

from __future__ import annotations

import json
import threading
import time

import pytest

from harness.agent.loop import Agent
from harness.agent.types import AgentResult, LLMResponse, ToolCall
from harness.evals.runner import score
from harness.packs.supervisor.tools import build_tools
from harness.tools.registry import ToolRegistry

DELAY = 0.4  # 가짜 비서 한 명이 답하는 데 걸리는 시간(초)


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeAgents:
    """A2AClient 가 쓰는 http(get/post)만 흉내 낸다. 비서(url)마다 카드와 '어떻게 답할지' 를 둔다.
    behavior: "ok" → completed, "approval" → input-required, "boom" → 서버 오류. 같은 비서로 들어온 질문은 들어온 순서대로 기록한다."""

    def __init__(self, agents: dict[str, str], delay: float = DELAY):
        self.agents = agents  # url → behavior
        self.delay = delay
        self.calls: list[tuple[str, str, str | None, float]] = []  # (url, question, contextId, 시작 시각)
        self.lock = threading.Lock()
        self.n = 0

    def get(self, url, headers=None):
        base = url.split("/.well-known")[0]
        if base not in self.agents:
            return _Resp({}, 404)
        name = base.rsplit(":", 1)[-1]
        return _Resp(
            {
                "name": f"비서{name}",
                "description": "",
                "url": base + "/a2a",
                "metadata": {"pack": f"p{name}", "aliases": [f"별칭{name}"]},
                "skills": [],
            }
        )

    def post(self, url, json=None, headers=None):
        base = url.rsplit("/a2a", 1)[0]
        behavior = self.agents[base]
        msg = json["params"]["message"]
        with self.lock:
            self.n += 1
            n = self.n
            self.calls.append((base, msg["parts"][0]["text"], msg.get("contextId"), time.perf_counter()))
        time.sleep(self.delay)
        if behavior == "unauthorized":  # 인증이 켜진 비서: JSON-RPC 봉투가 아닌 HTTP 401
            return _Resp({"detail": "API 키가 필요합니다"}, 401)
        if behavior == "boom":
            return _Resp({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "비서 내부 오류"}})
        task = {"id": f"task_{n}", "contextId": f"ctx_{base[-2:]}", "status": {"state": "completed"}, "artifacts": []}
        if behavior == "approval":
            task["status"] = {
                "state": "input-required",
                "message": {"parts": [{"kind": "text", "text": "create_note 결재 대기: 회사 비서 결재함에서 승인"}]},
            }
        else:
            task["artifacts"] = [{"parts": [{"kind": "text", "text": f"{msg['parts'][0]['text']} → 답 {n}"}]}]
        return _Resp({"jsonrpc": "2.0", "id": 1, "result": task})


def _registry(fake: FakeAgents) -> ToolRegistry:
    return ToolRegistry(build_tools(agents=list(fake.agents), http=fake, index=None))


def test_parallel_delegation_is_faster_than_sequential():
    fake = FakeAgents({"http://a:11": "ok", "http://b:22": "ok"})
    reg = _registry(fake)
    reg.get("list_agents").run({})  # 카드 조회를 미리 끝내 시간 측정에서 뺀다
    t0 = time.perf_counter()
    seq = [
        reg.get("delegate").run({"agent": a, "question": q}) for a, q in (("p11", "지연 몇 건"), ("p22", "차단 주파수"))
    ]
    t_seq = time.perf_counter() - t0
    t0 = time.perf_counter()
    out = reg.get("delegate_many").run(
        {"items": [{"agent": "p11", "question": "지연 몇 건"}, {"agent": "별칭22", "question": "차단 주파수"}]}
    )
    t_par = time.perf_counter() - t0
    assert t_seq >= 2 * DELAY * 0.95 and t_par < 1.5 * DELAY, (t_seq, t_par)  # 순차 0.8s+ vs 동시 0.6s 미만
    assert out["parallel"] == 2 and out["elapsed_s"] < 1.5 * DELAY
    # 결과는 items 순서, 출처(agent)·태스크 id·답이 항목마다 그대로 남는다
    assert [r["agent"] for r in out["results"]] == ["p11", "p22"]
    assert all(r["state"] == "completed" and "→ 답" in r["answer"] and r["task_id"] for r in out["results"])
    assert out["delegated"] == [{"agent": "p11", "state": "completed"}, {"agent": "p22", "state": "completed"}]
    assert "note" not in out
    # 두 요청이 겹쳐서 시작했다(동시)
    starts = sorted(t for *_, t in fake.calls[-2:])
    assert starts[1] - starts[0] < DELAY / 2
    assert [r["state"] for r in seq] == ["completed", "completed"]


def test_partial_input_required_is_reported_not_dropped():
    fake = FakeAgents({"http://a:11": "approval", "http://b:22": "ok"})
    out = (
        _registry(fake)
        .get("delegate_many")
        .run({"items": [{"agent": "p11", "question": "주문 1번에 메모"}, {"agent": "p22", "question": "차단 주파수"}]})
    )
    waiting, done = out["results"]
    assert waiting["state"] == "input-required" and "결재" in waiting["waiting_for"] and "결재 대기" in waiting["note"]
    assert done["state"] == "completed" and "차단 주파수 → 답" in done["answer"]
    assert out["delegated"] == [{"agent": "p11", "state": "input-required"}, {"agent": "p22", "state": "completed"}]
    assert "결재 대기 항목: p11" in out["note"] and "나머지 항목의 답은 그대로 전달" in out["note"]


def test_same_agent_items_run_in_order_and_failures_keep_partial_results():
    fake = FakeAgents({"http://a:11": "ok", "http://b:22": "boom"})
    reg = _registry(fake)
    reg.get("list_agents").run({})
    t0 = time.perf_counter()
    out = reg.get("delegate_many").run(
        {
            "items": [
                {"agent": "p11", "question": "첫 질문"},
                {"agent": "p11", "question": "앞 답을 이어서"},
                {"agent": "p22", "question": "터지는 비서"},
                {"agent": "없는비서", "question": "x"},
            ]
        }
    )
    elapsed = time.perf_counter() - t0
    # 같은 비서(p11)의 두 항목은 순차(0.8s), 다른 비서는 그와 동시 → 전체는 0.8s 안팎이고 1.2s(전부 순차)보다는 짧다
    assert 2 * DELAY * 0.95 <= elapsed < 3 * DELAY, elapsed
    a_calls = [c for c in fake.calls if c[0] == "http://a:11"]
    assert [c[1] for c in a_calls] == ["첫 질문", "앞 답을 이어서"]
    assert a_calls[0][2] is None and a_calls[1][2] == "ctx_11"  # 두 번째는 첫 답의 contextId 를 이어 받는다
    assert a_calls[1][3] - a_calls[0][3] >= DELAY * 0.95  # 앞 답을 받은 뒤에 보냈다
    states = [r["state"] for r in out["results"]]
    assert states == ["completed", "completed", "error", "error"]
    assert "비서 내부 오류" in out["results"][2]["error"] and "비서를 찾지 못했습니다" in out["results"][3]["error"]
    assert out["parallel"] == 2 and "답을 받지 못한 항목: p22" in out["note"]


class _Once:
    model = "fake"

    def __init__(self, script):
        self.script = list(script)

    def complete(self, messages, tools=None, **_):
        return self.script.pop(0) if self.script else LLMResponse(text="끝")


def test_loop_preview_and_scoring_accept_delegate_many():
    """루프 한 스텝의 delegate_many 호출이 tool_log 미리보기(300자)에 두 비서를 모두 남기고, 평가의 expect_calls 가 그걸 인정한다."""
    fake = FakeAgents({"http://a:11": "ok", "http://b:22": "ok"}, delay=0.01)
    call = ToolCall(
        id="c1",
        name="delegate_many",
        arguments={"items": [{"agent": "p11", "question": "지연 " * 60}, {"agent": "p22", "question": "주파수 " * 60}]},
    )
    prov = _Once([LLMResponse(tool_calls=[call]), LLMResponse(text="255건, 1.59 kHz. 출처: p11, p22")])
    res = Agent(prov, _registry(fake), "sys").run("둘 다 알려줘")
    assert res.steps == 2 and res.tool_calls[0]["name"] == "delegate_many" and res.tool_calls[0]["status"] == "ok"
    preview = res.tool_calls[0]["result_preview"]
    assert '"agent": "p11"' in preview and '"agent": "p22"' in preview and len(preview) <= 300
    task = {
        "id": "t",
        "type": "compound",
        "question": "q",
        "expect_calls": [
            {"name": ["delegate", "delegate_many"], "result_contains": {"agent": "p11"}},
            {"name": "delegate|delegate_many", "result_contains": {"agent": "p22"}},
        ],
    }
    assert score(task, res, [])["pass"]
    only_delegate = {**task, "expect_calls": [{"name": "delegate", "result_contains": {"agent": "p11"}}]}
    assert not score(only_delegate, res, [])["pass"]
    # 결과 원본은 화면 카드용으로 이벤트에 그대로 실린다(인용 카드 하나씩)
    ev = next(e for e in res.events if e.kind == "tool_result")
    assert [x["agent"] for x in ev.data["result"]["results"]] == ["p11", "p22"]
    assert json.loads(json.dumps(ev.data["result"]))["parallel"] == 2


def test_http_error_from_one_agent_is_a_readable_partial_failure():
    """하위 비서 하나가 401 을 돌려주면 그 항목만 'A2A HTTP 401' 오류로 남고 다른 비서의 답은 그대로다(KeyError 로 묶음이 죽지 않는다)."""
    fake = FakeAgents({"http://a:11": "ok", "http://b:22": "unauthorized"}, delay=0.01)
    out = (
        _registry(fake)
        .get("delegate_many")
        .run({"items": [{"agent": "p11", "question": "지연"}, {"agent": "p22", "question": "주파수"}]})
    )
    ok, bad = out["results"]
    assert ok["state"] == "completed" and bad["state"] == "error"
    assert "A2A HTTP 401" in bad["error"] and "API 키" in bad["error"]
    assert "답을 받지 못한 항목: p22" in out["note"]


def test_parallel_switch_off_hides_tool_and_prompt(monkeypatch):
    """HARNESS_SUPERVISOR_PARALLEL=0: 순차 대조 실험·되돌리기용. 도구 목록에서 delegate_many 가 빠지고 프롬프트도 예전 문구다."""
    import importlib

    import harness.packs.supervisor.tools as mod

    monkeypatch.setenv("HARNESS_SUPERVISOR_PARALLEL", "0")
    fake = FakeAgents({"http://a:11": "ok"})
    names = [tl.name for tl in mod.build_tools(agents=list(fake.agents), http=fake, index=None)]
    assert "delegate_many" not in names and "delegate" in names
    assert "delegate_many" not in mod.SYSTEM_PROMPT_SEQUENTIAL and "독립 질의는 한 번에 보내라" in mod.SYSTEM_PROMPT
    monkeypatch.delenv("HARNESS_SUPERVISOR_PARALLEL")
    importlib.reload(mod)
    assert "delegate_many" in [tl.name for tl in mod.build_tools(agents=list(fake.agents), http=fake, index=None)]


def test_delegate_many_rejects_empty_items():
    fake = FakeAgents({"http://a:11": "ok"})
    with pytest.raises(Exception):
        _registry(fake).get("delegate_many").run({"items": []})


def test_expect_calls_name_list_matches_plain_result():
    """기존 결과(delegate 만 쓴 실행)도 새 yaml(name 목록)로 그대로 통과한다 → 순차 결과와 비교가 성립한다."""
    res = AgentResult(
        answer="a",
        steps=2,
        stopped_reason="final",
        tool_calls=[{"name": "delegate", "status": "ok", "arguments": {}, "result_preview": '{"agent": "spice"}'}],
    )
    task = {
        "id": "r",
        "type": "route",
        "question": "q",
        "expect_calls": [{"name": ["delegate", "delegate_many"], "result_contains": {"agent": "spice"}}],
    }
    assert score(task, res, [])["pass"]
