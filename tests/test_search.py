from harness.packs.enterprise.search import DocIndex
from harness.packs.enterprise.tools import DOCS_DIR


def test_search_finds_right_doc():
    idx = DocIndex(DOCS_DIR)
    assert len(idx.list_docs()) == 10
    top = idx.search("지연 보상 승인자", k=3)
    assert any(h["doc"] == "배송 지연 보상 규정" for h in top[:2])
    top2 = idx.search("할인 승인 누가", k=3)
    assert top2[0]["doc"] == "할인 승인 규정"
    top3 = idx.search("취소 금지 AI", k=3)
    assert any(h["doc"] == "주문 취소 규정" for h in top3)


def test_search_docs_previews_long_sections_and_read_doc_returns_full_text(tmp_path, seeded_db):
    """ADR-033: 실문서처럼 절이 1,800자면 k=5 전문은 루프의 도구 결과 상한(4,000자)을 넘어 JSON 중간에서 잘렸다.
    검색은 앞부분만(k 에 따라 600자 이하), 전문은 read_doc 으로. k=10 이어도 상한 안에 든다."""
    import json

    import pytest

    from harness.packs.enterprise.tools import build_tools
    from harness.retrieval.citation import extract_sources
    from harness.tools.registry import ToolRegistry

    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(10):
        body = " ".join(f"휴가 신청 절차 {i} 항목 {j}" for j in range(150))  # 약 2,000자
        (docs / f"doc{i}.md").write_text(
            f"# 인사 규정 {i}\n\n## 휴가\n\n{body}\n\n## 출장\n\n출장 규정 본문.\n", encoding="utf-8"
        )
    db, _ = seeded_db
    reg = ToolRegistry(build_tools(db, index=DocIndex(docs)))
    for k in (5, 10):
        out = reg.get("search_docs").run({"query": "휴가 신청 절차", "k": k})
        assert len(out["hits"]) == k and all(h["text"].endswith("…") and h["chars"] > 1500 for h in out["hits"])
        assert len(json.dumps(out, ensure_ascii=False)) <= 4000, k
    hit = out["hits"][0]
    full = reg.get("read_doc").run({"section": hit["source"]})
    assert full["chars"] == hit["chars"] and full["text"].startswith("휴가 신청 절차") and "…" not in full["text"]
    assert extract_sources(full) == [{"doc": hit["doc"], "section": "휴가", "section_id": ""}]
    whole = reg.get("read_doc").run({"section": "인사 규정 3"})
    assert whole["section"] == "(전체)" and whole["sections"] == ["휴가", "출장"] and "출장 규정 본문" in whole["text"]
    with pytest.raises(ValueError):
        reg.get("read_doc").run({"section": "없는 문서 > 없는 절"})
