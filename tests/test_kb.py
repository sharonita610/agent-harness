"""지식 베이스: 작은 PDF(책갈피 포함)를 만들어 절 단위 청킹 → SQLite 저장 → BM25/벡터/하이브리드 검색 → 증분 임베딩을 검증한다."""
import hashlib
import math

import pytest

from harness.retrieval.kb import KnowledgeBase, ingest_markdown_dir, ingest_pdf

pymupdf = pytest.importorskip("pymupdf")


def _fake_embedder(texts):
    """결정적 가짜 임베딩: 단어 해시를 256차원에 누적(충돌을 줄여 순위가 뒤집히지 않게). 같은 단어를 공유하면 가깝다."""
    out = []
    for t in texts:
        v = [0.0] * 256
        for w in t.lower().split():
            h = int(hashlib.md5(w.encode()).hexdigest(), 16)
            v[h % 256] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


@pytest.fixture
def small_pdf(tmp_path):
    doc = pymupdf.open()
    pages = [
        ["Chapter 1", "Introduction", "1.1\nAnalysis types", "The simulator supports ac dc and tran analyses. The .ac line sets a frequency sweep.",
         "1.2\nUnits", "Scale factors: k means kilo, m means milli, meg means mega. 1M is one milli ohm."],
        ["1.3\nMeasurements", "The meas command finds crossing points with WHEN and values with FIND AT.", "More text about trig targ rise time."],
        ["Chapter 2", "Devices", "2.1\nResistors", "RXXXX n+ n- value. A resistor line names two nodes and a value."],
    ]
    for lines in pages:
        page = doc.new_page(width=595, height=842)
        y = 60
        for ln in lines:
            page.insert_text((70, y), ln, fontsize=11)
            y += 40 if "\n" not in ln else 50
        page.insert_text((290, 810), str(doc.page_count), fontsize=9)  # 쪽번호(바닥)
    doc.set_toc([[1, "1 Introduction", 1], [2, "1.1 Analysis types", 1], [2, "1.2 Units", 1], [2, "1.3 Measurements", 2], [1, "2 Devices", 3], [2, "2.1 Resistors", 3]])
    path = tmp_path / "manual.pdf"
    doc.save(str(path))
    return path


def test_ingest_pdf_splits_by_bookmarks(small_pdf):
    chunks = ingest_pdf(small_pdf, "test manual", min_level=1)
    by_id = {c.section_id: c for c in chunks}
    assert {"1.1", "1.2", "1.3", "2.1"} <= set(by_id)
    assert "frequency sweep" in by_id["1.1"].text and "meg means mega" in by_id["1.2"].text and "WHEN" in by_id["1.3"].text
    assert by_id["1.3"].page_start == 2 and by_id["2.1"].title_path.startswith("2 Devices > 2.1 Resistors")
    assert not any(c.text.strip().isdigit() for c in chunks)  # 쪽번호는 본문이 아니다
    assert by_id["1.1"].source == "test manual > 1.1 Analysis types (p.1)"


def test_kb_search_modes_and_incremental_embedding(small_pdf, tmp_path):
    kb = KnowledgeBase(tmp_path / "kb.sqlite")
    kb.build(ingest_pdf(small_pdf, "test manual", min_level=1), kind="pdf")
    assert kb.stats()["chunks"] >= 4 and kb.mode == "bm25"
    hits = kb.search("meg mega milli scale factors", k=2, mode="bm25")
    assert hits[0]["section_id"] == "1.2" and hits[0]["mode"] == "bm25" and "p.1" in hits[0]["source"]
    assert kb.embed("fake", _fake_embedder) >= 4 and kb.embed("fake", _fake_embedder) == 0  # 두 번째는 증분 0
    kb2 = KnowledgeBase(tmp_path / "kb.sqlite", embedder=_fake_embedder, embed_model="fake")
    assert kb2.mode == "hybrid" and kb2.coverage("fake") == 1.0
    assert kb2.search("WHEN FIND crossing meas", k=1, mode="vector")[0]["section_id"] == "1.3"
    assert kb2.search("resistor line nodes value", k=1, mode="hybrid")[0]["section_id"] == "2.1"
    sec = kb2.get_section("1.2")
    assert sec and "one milli ohm" in sec["text"] and sec["source"].startswith("test manual > 1.2 Units")
    assert kb2.get_section("Measurements")["source"].startswith("test manual > 1.3")
    assert kb2.get_section("9.9") is None


def test_kb_rebuild_keeps_embeddings_for_unchanged_chunks(small_pdf, tmp_path):
    kb = KnowledgeBase(tmp_path / "kb.sqlite")
    chunks = ingest_pdf(small_pdf, "test manual", min_level=1)
    kb.build(chunks)
    kb.embed("fake", _fake_embedder)
    kb.build(chunks)  # 같은 내용으로 다시 만들어도
    assert kb.embed("fake", _fake_embedder) == 0 and kb.coverage("fake") == 1.0


def test_markdown_docs_join_the_kb(tmp_path):
    from harness.packs.spice.tools import DOCS_DIR
    kb = KnowledgeBase(tmp_path / "kb.sqlite")
    kb.build(ingest_markdown_dir(DOCS_DIR), kind="markdown")
    assert any(d["kind"] == "markdown" for d in kb.list_docs())
    hit = kb.search("1M 메가 밀리", k=1, mode="bm25")[0]
    assert hit["section_id"] == "" and "밀리" in hit["text"]
