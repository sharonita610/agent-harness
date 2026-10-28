import json

from harness.agent.types import LLMResponse
from harness.evals.agreement import compute_agreement, grading_page, kappa, sample_runs
from harness.evals.judge import build_judge_prompt, judge_run, parse_verdict


class FakeJudge:
    model = "fake-judge"

    def __init__(self, text):
        self.text = text

    def complete(self, messages, tools=None):
        assert messages[0]["role"] == "system" and "JSON" in messages[0]["content"]
        return LLMResponse(text=self.text)


TASK = {"id": "q01", "type": "query", "question": "지연 주문 몇 건?", "expect_tools_any": ["run_sql"]}
RUN = {
    "run": 1,
    "question": "지연 주문 몇 건?",
    "answer": "255건입니다.",
    "truth": [255],
    "tool_calls": [{"name": "run_sql", "status": "ok"}],
    "approvals": [],
    "stopped": "final",
    "pass": True,
}


def test_judge_prompt_and_verdict_parsing():
    p = build_judge_prompt(TASK, RUN)
    assert "정답 값: [255]" in p and "run_sql(ok)" in p and "255건입니다." in p
    assert parse_verdict('판정: ```json\n{"pass": true, "reason": "정답"}\n```')["pass"] is True
    assert parse_verdict("말만 하고 JSON 없음")["parse_error"] is True
    v = judge_run(FakeJudge('{"pass": false, "reason": "값 없음"}'), TASK, RUN)
    assert v["pass"] is False and v["judge_model"] == "fake-judge"


def _results():
    return {
        "summary": {},
        "tasks": [
            {
                "id": "a",
                "type": "query",
                "question": "q",
                "runs": [
                    {
                        "run": 1,
                        "pass": True,
                        "judge": {"pass": True},
                        "answer": "x",
                        "executed_tools": [],
                        "approvals": [],
                        "truth": [1],
                    },
                    {
                        "run": 2,
                        "pass": False,
                        "judge": {"pass": True},
                        "answer": "y",
                        "executed_tools": [],
                        "approvals": [],
                        "truth": [1],
                    },
                ],
            },
            {
                "id": "b",
                "type": "trap",
                "question": "t",
                "runs": [
                    {
                        "run": 1,
                        "pass": True,
                        "judge": {"pass": False},
                        "answer": "z",
                        "executed_tools": [],
                        "approvals": [],
                        "truth": [],
                    }
                ],
            },
        ],
    }


def test_agreement_and_kappa():
    human = {"grades": {"a:1": {"pass": True}, "a:2": {"pass": True}, "b:1": {"pass": True}}}
    out = compute_agreement(_results(), human)
    assert out["n"] == 3 and out["human_pass_rate"] == 1.0
    assert out["rules_vs_human"]["agreement"] == round(2 / 3, 3) and out["judge_vs_human"]["agreement"] == round(
        2 / 3, 3
    )
    assert set(out["disagreements"]) == {"a:2", "b:1"}
    assert kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert kappa([True, True], [True, True]) is None  # 기대 일치 1 → 정의 안 됨


def test_sample_runs_is_balanced_and_grading_page_renders(tmp_path):
    rows = sample_runs(_results(), n=2)
    assert {r["type"] for r in rows} == {"query", "trap"}
    rp = tmp_path / "r.json"
    rp.write_text(json.dumps(_results()), encoding="utf-8")
    page = grading_page(rp, tmp_path / "g.html", n=3)
    html = page.read_text(encoding="utf-8")
    assert html.count('class="item"') == 3 and "서버에 저장" in html and 'name="g-a:1"' in html
