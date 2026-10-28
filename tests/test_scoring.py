from harness.agent.types import AgentResult
from harness.evals.runner import normalize, score


def _res(answer, tools=("search_docs",)):
    return AgentResult(
        answer=answer, steps=2, stopped_reason="final", tool_calls=[{"name": t, "status": "ok"} for t in tools]
    )


def test_normalize_special_spaces_and_percent():
    assert normalize("주문 금액의 **5 %** 입니다") == "주문 금액의 5% 입니다"
    assert normalize("“(유) 네오”") == '"(유) 네오"'


def test_percent_with_nnbsp_passes_contains_check():
    task = {"id": "x", "type": "doc", "answer_contains_any": ["5%"]}
    assert score(task, _res("보상 비율은 **5 %**입니다."), [])["pass"]


def test_truth_string_with_nnbsp_passes():
    task = {"id": "y", "type": "query", "expect_tools_any": ["run_sql"]}
    assert score(task, _res("거래처는 “(유) 네오”입니다.", tools=("run_sql",)), ["(유) 네오"])["pass"]


def test_real_wrong_answer_still_fails():
    task = {"id": "z", "type": "doc", "answer_contains_any": ["영업부서장", "부서장"]}
    assert not score(task, _res("승인자는 영업팀장입니다."), [])["pass"]
