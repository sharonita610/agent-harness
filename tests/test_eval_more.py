"""확장 평가 묶음: 다중 턴 실행, 태스크별 범위, 새 채점 검사, {변수} 치환."""

from __future__ import annotations

import sqlite3

import pytest
import yaml

from harness.agent.types import AgentResult, LLMResponse, ToolCall
from harness.evals import runner
from harness.evals.runner import _fill, prepare, run_turns, score
from tests.test_loop import FakeProvider


def _res(answer, approvals=(), tools=(), stopped="final"):
    return AgentResult(
        answer=answer,
        steps=1,
        stopped_reason=stopped,
        tool_calls=[{"name": t, "status": "ok"} for t in tools],
        approvals=[{"step": 1, "name": n, "arguments": a, "approved": False} for n, a in approvals],
    )


def test_fill_substitutes_everywhere():
    task = {
        "turns": ["주문 {oid}번", "메모"],
        "expect_approval_args": [{"name": "create_note", "args_contains": {"order_id": "{oid}"}}],
    }
    out = _fill(task, {"oid": "353"})
    assert out["turns"][0] == "주문 353번" and out["expect_approval_args"][0]["args_contains"]["order_id"] == "353"


def test_prepare_joins_turns_and_fills_params(seeded_db):
    db, _ = seeded_db
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    task = {
        "id": "x",
        "turns": ["주문 {oid}번 상태", "메모 남겨줘"],
        "params_sql": {"oid": "SELECT id FROM orders WHERE status='지연' ORDER BY id LIMIT 1"},
        "truth_sql": "SELECT COUNT(*) FROM customers WHERE grade IN ('B','C')",
        "expect_approval_args": [{"name": "create_note", "args_contains": {"order_id": "{oid}"}}],
    }
    filled, question, truth = prepare(task, conn)
    oid = conn.execute("SELECT id FROM orders WHERE status='지연' ORDER BY id LIMIT 1").fetchone()[0]
    assert question == f"주문 {oid}번 상태 → 메모 남겨줘" and filled["question"] == question
    assert filled["expect_approval_args"][0]["args_contains"]["order_id"] == str(oid)
    assert truth == [conn.execute("SELECT COUNT(*) FROM customers WHERE grade IN ('B','C')").fetchone()[0]]
    assert "params_sql" not in filled


def test_score_approval_args_and_regex_all():
    task = {
        "expect_approval_args": [{"name": "create_note", "args_contains": {"order_id": "353", "note": "5%"}}],
        "answer_regex_all": ["10월|2026-10", "4[34](\\.\\d+)?\\s*%"],
    }
    ok = _res("10월에 43.9 % 였다", approvals=[("create_note", {"order_id": 353, "note": "보상 5% 안내"})])
    assert score(task, ok, [])["pass"]
    bad_args = _res("10월에 43.9%", approvals=[("create_note", {"order_id": 353, "note": "보상 안내"})])
    assert score(task, bad_args, [])["checks"]["approval_args"] is False
    bad_text = _res("9월에 43.9%", approvals=[("create_note", {"order_id": 353, "note": "5%"})])
    assert score(task, bad_text, [])["checks"]["answer_patterns"] is False


def test_run_turns_passes_history_and_merges(seeded_db):
    from harness.agent.loop import Agent
    from harness.packs.enterprise.tools import DOCS_DIR, build_tools
    from harness.tools.permissions import auto_deny
    from harness.tools.registry import ToolRegistry

    db, _ = seeded_db
    prov = FakeProvider([LLMResponse(text="255건입니다"), LLMResponse(text="A등급은 43건입니다")])
    agent = Agent(prov, ToolRegistry(build_tools(db_path=db, docs_dir=DOCS_DIR)), "sys", approval_handler=auto_deny)
    res, turns = run_turns(agent, ["지연 주문 몇 건?", "그중 A등급은?"])
    assert res.answer == "A등급은 43건입니다" and res.steps == 2 and len(turns) == 2
    second = prov.seen[1]  # 둘째 턴이 받은 메시지: system, 첫 질문, 첫 답, 둘째 질문
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[1]["content"] == "지연 주문 몇 건?" and second[2]["content"] == "255건입니다"
    single, none = run_turns(agent, ["한 번만"])
    assert none == [] and single.answer == "끝"


def test_run_eval_multiturn_and_scope(seeded_db, tmp_path, monkeypatch):
    """러너가 turns 를 history 로 잇고, scope 태스크는 범위 레지스트리·프롬프트로 따로 돈다."""
    db, _ = seeded_db
    tasks = [
        {
            "id": "mt01",
            "type": "multiturn",
            "turns": ["지연 몇 건?", "그중 A등급?"],
            "truth_value": 43,
        },
        {
            "id": "pr04",
            "type": "premise",
            "scope": "region=부산",
            "question": "지연 몇 건?",
            "truth_value": 39,
            "expect_tools_any": ["run_sql"],
        },
    ]
    tp = tmp_path / "t.yaml"
    tp.write_text(yaml.safe_dump(tasks, allow_unicode=True), encoding="utf-8")
    script = [
        LLMResponse(text="255건"),
        LLMResponse(text="A등급은 43건"),
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="c1", name="run_sql", arguments={"sql": "SELECT COUNT(*) AS n FROM orders WHERE status='지연'"}
                )
            ]
        ),
        LLMResponse(text="부산 범위에서 39건"),
    ]
    fake = FakeProvider(script)
    monkeypatch.setattr(runner, "make_provider", lambda spec, **kw: fake)
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
    out = runner.run_eval(provider="local", n=1, tasks_path=tp, db_path=db, tag="t")
    rows = {t["id"]: t for t in out["tasks"]}
    assert rows["mt01"]["pass_rate"] == 1.0 and rows["mt01"]["runs"][0]["turns"][0]["answer"] == "255건"
    assert rows["mt01"]["question"] == "지연 몇 건? → 그중 A등급?"
    pr = rows["pr04"]
    assert pr["scope"] == "region=부산" and pr["pass_rate"] == 1.0
    # 범위 레지스트리로 실행된 SQL 은 부산 거래처만 센다(39)
    sql_call = next(c for c in pr["runs"][0]["tool_calls"] if c["name"] == "run_sql")
    assert '"n": 39' in sql_call["result_preview"]
    # 범위 태스크의 시스템 프롬프트에는 범위 안내가 붙는다
    scoped_prompt = fake.seen[2][0]["content"]
    assert (
        "부산" in scoped_prompt and "데이터 범위" in scoped_prompt and "데이터 범위" not in fake.seen[0][0]["content"]
    )
    assert out["summary"]["tasks_file"] == "t"


@pytest.mark.parametrize("path", ["tasks_enterprise_more.yaml", "tasks_spice_more.yaml", "tasks_factory_more.yaml"])
def test_extended_task_files_are_well_formed(path):
    from pathlib import Path

    tasks = yaml.safe_load((Path(runner.__file__).parent / path).read_text(encoding="utf-8"))
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)) and all(t.get("question") or t.get("turns") for t in tasks)
    assert all(t["type"] in {"multiturn", "workflow", "trend", "premise", "design", "variant"} for t in tasks)
    assert all(not t.get("turns") or len(t["turns"]) >= 2 for t in tasks)


def test_approval_args_accept_alternatives():
    task = {
        "expect_approval_args": [
            {"name": "create_action", "args_contains": {"station": ["화성", "16"], "owner": "이엔지니어"}}
        ]
    }
    by_name = _res("", approvals=[("create_action", {"line": 1, "station": "화성", "owner": "이엔지니어"})])
    by_id = _res("", approvals=[("create_action", {"line": 1, "station": "16", "owner": "이엔지니어"})])
    other = _res("", approvals=[("create_action", {"line": 1, "station": "권취", "owner": "이엔지니어"})])
    assert score(task, by_name, [])["checks"]["approval_args"] and score(task, by_id, [])["checks"]["approval_args"]
    assert score(task, other, [])["checks"]["approval_args"] is False


def test_provider_slug_keeps_dots_out_of_result_names():
    from harness.evals.runner import _provider_slug

    assert (
        _provider_slug("compat:http://127.0.0.1:8091/v1|mlx-community/gpt-oss-20b-MXFP4-Q4")
        == "compat-gpt-oss-20b-MXFP4-Q4"
    )
    assert _provider_slug("route:local->openai:gpt-5-mini") == "route-local-to-openai-gpt-5-mini"
    assert _provider_slug("local") == "local" and _provider_slug("openai:gpt-5-mini") == "openai-gpt-5-mini"
