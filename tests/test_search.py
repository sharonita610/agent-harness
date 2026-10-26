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
