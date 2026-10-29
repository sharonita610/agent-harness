"""문서 그래프(ADR-023): 규정 문서 사이의 참조·주제·데이터 관계와 도구."""

from __future__ import annotations

import pytest

from harness.packs.enterprise.tools import DOCS_DIR, build_tools, make_graph
from harness.retrieval.docgraph import DocGraph, title_aliases


def test_title_aliases():
    assert title_aliases("배송 지연 보상 규정") == ["배송 지연 보상 규정", "배송 지연 보상", "보상 규정"]
    aliases = title_aliases("반품 및 교환 규정")
    assert "반품 규정" in aliases and "교환 규정" in aliases
    assert "처리 절차" not in title_aliases("재고 부족 처리 절차")  # 흔한 말로 시작하는 별칭은 버린다
    assert title_aliases("일정") == []


def test_build_finds_number_title_alias_and_section_references():
    g = make_graph()
    assert len(g.nodes) == 10 and g.nodes["CS-002"].team == "CS팀" and g.nodes["CS-002"].revised == "2026-05-10"
    kinds = {(e.src, e.dst, e.kind) for e in g.edges}
    assert ("WH-009", "WH-004", "참조") in kinds  # 번호 인용 "(WH-004)"
    assert ("WH-004", "CS-002", "참조") in kinds  # 짧은 제목 "보상 규정"
    assert ("OPS-010", "CS-005", "참조") in kinds  # "반품 규정" ← "반품 및 교환 규정"
    assert ("SALES-006", "SALES-003", "주제") in kinds  # 절 제목 "기본 할인"
    assert ("WH-004", "WH-009", "데이터") in kinds and ("WH-009", "WH-004", "데이터") in kinds  # 양방향
    assert g.stats() == {"docs": 10, "edges": 27, "by_kind": {"참조": 6, "주제": 1, "데이터": 20}}


def test_impact_of_compensation_policy_follows_citations_two_levels():
    r = make_graph().impact("배송 지연 보상 규정")
    ids = [x["doc"] for x in r["impacted"]]
    assert ids == ["OPS-010", "WH-004", "WH-009"]
    assert r["impacted"][2]["depth"] == 2 and "재고 부족 처리 절차" in r["impacted"][2]["via"]
    assert {x["doc"] for x in r["shares_data"]} == {"OPS-001", "SALES-006", "FIN-007"}
    assert "orders.delay_reason" in next(x for x in r["shares_data"] if x["doc"] == "FIN-007")["data"]
    assert r["count"] == 6


def test_related_lists_both_directions_and_concepts():
    r = make_graph().related("OPS-010")
    assert {x["doc"] for x in r["references"]} == {"CS-002", "CS-005"}
    assert {x["doc"] for x in r["referenced_by"]} == {"WH-004", "WH-009"}
    assert any(c["data"] == "order_status_history" for c in r["concepts"])
    assert r["shares_data_with"] and all(x["kind"] == "데이터" for x in r["shares_data_with"])


def test_resolve_by_number_title_alias_and_partial():
    g = make_graph()
    assert g.resolve("cs-002").id == "CS-002"
    assert g.resolve("반품 규정").id == "CS-005"
    assert g.resolve("창고").id == "WH-009"
    assert g.resolve("규정") is None  # 여럿에 걸리면 고르지 않는다
    with pytest.raises(ValueError, match="문서를 찾지 못했습니다"):
        g.related("없는 문서")


def test_svg_and_table():
    g = make_graph()
    svg = g.render_svg(focus="CS-002")
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "배송 지연 보상 규정" in svg and svg.count("<rect") == 11 and 'marker-end="url(#arrow)"' in svg
    rows = g.table()
    assert len(rows) == 10 and next(r for r in rows if r["문서"] == "OPS-010")["인용됨"] == 2


def test_generic_markdown_folder_without_headers(tmp_path):
    (tmp_path / "a.md").write_text("# 첫째 문서\n\n## 하나\n둘째 문서를 보라.\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# 둘째 문서\n\n## 둘\n본문.\n", encoding="utf-8")
    g = DocGraph.build(tmp_path, {})
    assert set(g.nodes) == {"a", "b"} and [(e.src, e.dst, e.kind) for e in g.edges] == [("a", "b", "참조")]
    assert g.impact("둘째 문서")["impacted"][0]["doc"] == "a"


def test_tools_registered_and_answer(seeded_db):
    db, _ = seeded_db
    tools = {t.name: t for t in build_tools(db_path=db, docs_dir=DOCS_DIR)}
    assert {"related_docs", "impact_of"} <= set(tools)
    out = tools["impact_of"].run({"name": "CS-002"})
    assert [x["doc"] for x in out["impacted"]][:2] == ["OPS-010", "WH-004"]
    rel = tools["related_docs"].run({"name": "창고 운영 지침"})
    assert {x["doc"] for x in rel["references"]} == {"WH-004", "OPS-010"}
