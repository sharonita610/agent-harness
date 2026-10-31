"""출처 인용 채점(cite_matches_retrieval): 검색 결과의 절 수집 → 답의 "출처:" 파싱 → 부분 일치 판정 → 러너 요약의 citation_accuracy."""

from __future__ import annotations

from pydantic import BaseModel

from harness.agent.loop import Agent
from harness.agent.types import AgentResult, LLMResponse
from harness.evals.runner import _summarize, render_markdown, score
from harness.retrieval.citation import (
    citation_matches,
    citations_ok,
    extract_sources,
    normalize_ref,
    parse_citations,
    sources_from_tool_calls,
)
from harness.tools.registry import Tool, ToolRegistry
from tests.test_loop import FakeProvider, tool_call

HITS = {
    "hits": [
        {"doc": "배송 지연 보상 규정", "section": "보상 비율", "text": "6~10일 5%"},
        {"doc": "할인 승인 규정", "section": "승인 권한", "text": "200만 원 영업부서장"},
    ]
}
MANUAL = [
    {
        "doc": "ngspice manual",
        "section": "AC: Small-Signal AC Analysis",
        "section_id": "11.3.1",
        "source": "ngspice manual > 11.3.1 AC: Small-Signal AC Analysis (p.145)",
    }
]


def test_extract_sources_from_search_results_only():
    assert extract_sources(HITS) == [
        {"doc": "배송 지연 보상 규정", "section": "보상 비율", "section_id": ""},
        {"doc": "할인 승인 규정", "section": "승인 권한", "section_id": ""},
    ]
    assert extract_sources(MANUAL)[0]["section_id"] == "11.3.1"
    # read_manual 처럼 source 문자열만 있는 결과도 절 번호·제목을 복원한다
    sec = extract_sources({"source": "ngspice manual > 11.3.1 AC: Small-Signal AC Analysis (p.145)", "text": "…"})
    assert sec == [{"doc": "ngspice manual", "section": "AC: Small-Signal AC Analysis", "section_id": "11.3.1"}]
    assert extract_sources({"rows": [{"id": 1}], "columns": ["id"]}) == []  # SQL 결과는 출처가 아니다
    assert extract_sources("문자열") == [] and extract_sources(None) == []


def test_normalize_strips_spaces_parens_and_markdown():
    assert (
        normalize_ref("**배송 지연 보상 규정** (2026 개정)") == "배송지연보상규정2026개정"
    )  # 괄호는 빼고 글자는 남긴다
    assert normalize_ref("“출처: AC: Small-Signal AC Analysis (p.145)”") == "ac:small-signalacanalysisp.145"


def test_parse_citations_formats():
    assert parse_citations("보상 비율은 5% 입니다.\n출처: 배송 지연 보상 규정 > 보상 비율") == [
        ("배송 지연 보상 규정", "보상 비율")
    ]
    assert parse_citations("5% 입니다 (출처: **배송 지연 보상 규정 > 보상 비율**).") == [
        ("배송 지연 보상 규정", "보상 비율")
    ]
    # 쉼표로 이어 쓴 절은 앞 항목의 문서명을 이어받고, 세미콜론 뒤는 새 문서
    assert set(parse_citations("출처: 배송 지연 보상 규정 > 보상 비율, 승인 권한; 할인 승인 규정 > 승인 권한")) == {
        ("배송 지연 보상 규정", "보상 비율"),
        ("배송 지연 보상 규정", "승인 권한"),
        ("할인 승인 규정", "승인 권한"),
    }
    # 꼬리표 없이 괄호·따옴표 안에 "문서 > 절" 만 적은 답(실제 gpt-oss 답변의 흔한 꼴)
    assert parse_citations(
        "보상 비율은 **5%**입니다.\n(배송 지연 보상 규정 > 보상 비율, “재고 부족이면 2%p 추가”)"
    ) == [("배송 지연 보상 규정", "보상 비율")]
    assert parse_citations("재고 부족은 회사 책임입니다. “주문 상태 정의 > 지연 사유 코드” 문서에 명시.") == [
        ("주문 상태 정의", "지연 사유 코드")
    ]
    assert parse_citations("(문서: **월말 마감 절차 > 마감 전 점검 항목**)") == [
        ("월말 마감 절차", "마감 전 점검 항목")
    ]
    assert parse_citations("> *인용문*\n`SELECT 1 WHERE a > b`\n조회 결과 5건") == []  # 블록 인용·코드는 인용이 아니다
    assert parse_citations("(출처: “배송 지연 보상 규정” > 보상 비율)") == [("배송 지연 보상 규정", "보상 비율")]
    assert (
        parse_citations("총 금액: **7,581,000원**\n\n> **메모 작성 요청**\n> 승인이 필요합니다") == []
    )  # 줄 머리 '>' 는 블록 인용
    assert parse_citations("**출처**\n- “주문 상태 정의 > 지연 사유 코드”: 재고 부족은 회사 책임") == [
        ("주문 상태 정의", "지연 사유 코드")
    ]
    assert parse_citations("출처: 배송 지연 보상 규정의 보상 비율 절") == [("", "배송 지연 보상 규정의 보상 비율 절")]
    assert parse_citations("출처 없음 그냥 답") == []  # 꼬리표 뒤에 ':' 가 없으면 꼬리표가 아니다
    assert parse_citations("그냥 답") == []


def test_citation_matching_rules():
    src = extract_sources(HITS)
    assert citations_ok("출처: 배송 지연 보상 규정 > 보상 비율", src)
    assert citations_ok("출처: 배송 지연 보상 규정 > 보상 비율 절", src)  # 절 제목 부분 일치
    assert citations_ok("출처: 배송 지연 보상 규정의 '보상 비율' 절", src)  # '>' 없이 한 줄
    assert citations_ok("출처: “배송 지연 보상 규정” 문서 (보상 비율 섹션)", src)  # 괄호 안 절 이름
    assert citations_ok("5% 입니다.\n(배송 지연 보상 규정 > 보상 비율)", src)  # 꼬리표 없는 괄호 인용
    assert citations_ok("출처: 배송 지연 보상 규정 > 보상 비율; 할인 승인 규정 > 승인 권한", src)
    assert not citations_ok("출처: 배송 지연 보상 규정", src)  # 절이 없다
    assert not citations_ok("출처: 반품 및 교환 규정 > 환불", src)  # 검색되지 않은 문서(지어낸 출처)
    assert not citations_ok("출처: 배송 지연 보상 규정 > 승인 권한", src)  # 문서는 맞지만 절이 다른 문서 것
    assert not citations_ok("보상 비율은 5% 입니다.", src)  # 출처 없음
    assert not citations_ok("출처: 배송 지연 보상 규정 > 보상 비율", [])  # 검색 결과가 없으면 어떤 인용도 거짓
    man = extract_sources(MANUAL)
    assert citation_matches(("ngspice manual", "11.3.1 AC: Small-Signal AC Analysis (p.145)"), man)
    assert citation_matches(("ngspice manual", "11.3.1"), man)  # 절 번호만
    assert not citation_matches(("ngspice manual", "11.3.10 Transient Analysis"), man)  # 접두 번호는 다른 절
    assert not citation_matches(("ngspice manual", "11"), man)


def test_loop_records_result_sources():
    class Q(BaseModel):
        query: str

    reg = ToolRegistry(
        [
            Tool("search_docs", "검색", Q, lambda a: HITS),
            Tool("run_sql", "조회", Q, lambda a: {"rows": [{"n": 1}]}),
        ]
    )
    prov = FakeProvider(
        [
            LLMResponse(tool_calls=[tool_call("search_docs", query="보상"), tool_call("run_sql", query="x")]),
            LLMResponse(text="5% 입니다. 출처: 배송 지연 보상 규정 > 보상 비율"),
        ]
    )
    res = Agent(prov, reg, "sys").run("보상?")
    by_name = {c["name"]: c for c in res.tool_calls}
    assert by_name["search_docs"]["result_sources"] == extract_sources(HITS)
    assert "result_sources" not in by_name["run_sql"]
    assert sources_from_tool_calls(res.tool_calls) == extract_sources(HITS)


def _res(answer, sources=None, status="ok"):
    call = {"name": "search_docs", "status": status}
    if sources is not None:
        call["result_sources"] = sources
    return AgentResult(answer=answer, steps=2, stopped_reason="final", tool_calls=[call])


def test_score_cite_check_only_when_task_asks():
    task = {"id": "d01", "type": "doc", "expect_tools_any": ["search_docs"], "answer_contains_any": ["5%"]}
    plain = score(task, _res("5% 입니다. 출처: 반품 규정 > 환불", extract_sources(HITS)), [])
    assert "cite_matches_retrieval" not in plain["checks"] and plain["pass"]
    task["expect_citation"] = True
    good = score(task, _res("**5%** 입니다.\n출처: 배송 지연 보상 규정 > 보상 비율", extract_sources(HITS)), [])
    assert good["checks"]["cite_matches_retrieval"] and good["pass"]
    assert good["citations"] == {
        "cited": ["배송 지연 보상 규정 > 보상 비율"],
        "retrieved": ["배송 지연 보상 규정 > 보상 비율", "할인 승인 규정 > 승인 권한"],
    }
    fake = score(task, _res("5% 입니다. 출처: 반품 규정 > 환불", extract_sources(HITS)), [])
    assert not fake["checks"]["cite_matches_retrieval"] and not fake["pass"]
    no_search = score(task, _res("5% 입니다. 출처: 배송 지연 보상 규정 > 보상 비율", None), [])
    assert not no_search["checks"]["cite_matches_retrieval"]  # 검색 없이 적은 출처는 지어낸 것


def test_summary_reports_citation_accuracy_separately():
    def run(ok, cite=None):
        checks = {"finished": True} | ({} if cite is None else {"cite_matches_retrieval": cite})
        return {
            "pass": ok,
            "checks": checks,
            "answer": "답",
            "steps": 2,
            "latency_s": 1.0,
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }

    rows = [
        {"id": "d01", "type": "doc", "pass_rate": 0.5, "runs": [run(True, True), run(False, False)]},
        {"id": "q01", "type": "query", "pass_rate": 1.0, "runs": [run(True), run(True)]},
    ]
    s = _summarize(rows, provider="local", model="m", n=2)
    assert s["pass_rate"] == 0.75 and s["citation_checked"] == 2 and s["citation_accuracy"] == 0.5
    assert "출처 정확도 50%" in render_markdown(s, [{**r, "question": "q"} for r in rows])
    s2 = _summarize([rows[1]], provider="local", model="m", n=2)
    assert "citation_accuracy" not in s2  # 출처 검사를 켠 태스크가 없으면 필드도 없다
