"""청크 크기 옵션(split_long, DocIndex max_chars, kb build --chunk-chars)과 융합 옵션(rrf_fuse 의 k·가중치)."""

from __future__ import annotations

import json
import os

from harness.evals import retrieval_eval
from harness.packs.enterprise.tools import DOCS_DIR
from harness.retrieval import DocIndex, rrf_fuse, split_long
from harness.retrieval.kb import MAX_CHUNK_CHARS, KnowledgeBase, ingest_markdown_dir
from harness.retrieval.search import RRF_K
from tests.test_hybrid_search import bag_embedder

PARA = "가" * 100 + "\n\n" + "나" * 100 + "\n\n" + "다" * 100


def test_split_long_keeps_paragraph_boundaries():
    assert split_long(PARA, 0) == [PARA] and split_long(PARA, 1000) == [PARA]
    parts = split_long(PARA, 210)
    assert parts == ["가" * 100 + "\n\n" + "나" * 100, "다" * 100]
    assert all(len(p) <= 210 for p in parts)
    lines = split_long("\n".join("x" * 50 for _ in range(6)), 120)  # 한 문단이 상한보다 길면 줄 단위로
    assert len(lines) == 3 and all(len(p) <= 120 for p in lines)


def test_docindex_max_chars_splits_sections_and_still_searches():
    base = DocIndex(DOCS_DIR)
    small = DocIndex(DOCS_DIR, max_chars=150)
    assert small.max_chars == 150 and len(small.chunks) > len(base.chunks)
    assert all(len(c.text) <= 150 or "\n" not in c.text for c in small.chunks)
    assert {(c.doc, c.section) for c in small.chunks} == {(c.doc, c.section) for c in base.chunks}  # 절은 그대로
    assert small.search("할인 승인 누가", k=3)[0]["doc"] == "할인 승인 규정"
    assert len(DocIndex(DOCS_DIR, max_chars=3600).chunks) == len(base.chunks)  # 절이 전부 짧으면 변화 없음


def test_ingest_markdown_dir_honours_chunk_chars():
    from harness.packs.spice.tools import DOCS_DIR as SPICE_DOCS

    default = ingest_markdown_dir(SPICE_DOCS)
    small = ingest_markdown_dir(SPICE_DOCS, max_chars=200)
    assert len(small) > len(default) and max(c.part for c in small) >= 1
    assert MAX_CHUNK_CHARS == int(os.getenv("HARNESS_CHUNK_CHARS") or 1800)  # 기본값은 그대로(환경변수로만 바뀐다)


def test_rrf_fuse_default_matches_formula_and_options_change_order():
    bm, vec = [1, 2, 3], [3, 1, 2]
    fused = dict(rrf_fuse(bm, vec))
    assert abs(fused[1] - (1 / (RRF_K + 1) + 1 / (RRF_K + 2))) < 1e-12
    assert rrf_fuse(bm, vec)[0][0] == 1  # 두 쪽 모두 상위인 1 이 1위
    assert rrf_fuse(bm, vec, vector_weight=0.0)[:3] == [(i, 1 / (RRF_K + r + 1)) for r, i in enumerate(bm)]
    assert [i for i, _ in rrf_fuse(bm, vec, bm25_weight=0.0)][:3] == vec
    # k 가 작으면 1위 근처의 순위 차가 커진다: 벡터 1위(3)와 BM25 1위(1)의 점수 차
    far = rrf_fuse([1, 2, 3], [3, 4, 5], rrf_k=1)
    near = rrf_fuse([1, 2, 3], [3, 4, 5], rrf_k=1000)
    assert dict(far)[1] / dict(far)[2] > dict(near)[1] / dict(near)[2]


def test_kb_search_accepts_fusion_options(tmp_path):
    from harness.packs.spice.tools import DOCS_DIR as SPICE_DOCS

    kb = KnowledgeBase(tmp_path / "kb.sqlite")
    kb.build(ingest_markdown_dir(SPICE_DOCS), kind="markdown")
    kb.embed("bag", bag_embedder)
    kb2 = KnowledgeBase(tmp_path / "kb.sqlite", embedder=bag_embedder, embed_model="bag")
    q = "1M 메가 밀리 접미사"
    bm = kb2.search(q, k=3, mode="bm25")
    hyb = kb2.search(q, k=3, mode="hybrid", rrf_k=10, bm25_weight=2.0, vector_weight=0.5)
    assert hyb and hyb[0]["mode"] == "hybrid"
    only_bm = kb2.search(q, k=3, mode="hybrid", vector_weight=0.0)
    assert [h["source"] for h in only_bm] == [h["source"] for h in bm]  # 벡터 가중치 0 이면 BM25 순위 그대로


def test_docindex_search_accepts_fusion_options(tmp_path):
    idx = DocIndex(DOCS_DIR, embedder=bag_embedder, embed_model="bag", cache_path=tmp_path / "e.json")
    bm = idx.search("지연 보상 승인자", k=3, mode="bm25")
    only_bm = idx.search("지연 보상 승인자", k=3, mode="hybrid", vector_weight=0.0, rrf_k=5)
    assert [h["section"] for h in only_bm] == [h["section"] for h in bm]


def test_retrieval_eval_records_chunk_and_fusion_options(tmp_path, monkeypatch):
    monkeypatch.setattr(retrieval_eval, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(retrieval_eval, "embedder_available", lambda *a, **k: False)
    out = retrieval_eval.run(pack="enterprise", chunk_chars=150, rrf_k=20, bm25_weight=2.0, tag="c150")
    path = tmp_path / out["path"].split("/")[-1]
    assert path.name.endswith("_c150.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["chunk_chars"] == 150 and data["chunks"] > 42 and data["index"] == "docs"
    assert data["fusion"] == {"rrf_k": 20, "bm25_weight": 2.0, "vector_weight": 1.0}
    assert [r["mode"] for r in data["results"]] == ["bm25"] and data["results"][0]["n"] == 18
    monkeypatch.setenv("HARNESS_CHUNK_CHARS", "200")
    out2 = retrieval_eval.run(pack="enterprise")
    data2 = json.loads((tmp_path / out2["path"].split("/")[-1]).read_text(encoding="utf-8"))
    assert data2["chunk_chars"] == 200 and data2["fusion"]["rrf_k"] == RRF_K and data2["tag"] is None
