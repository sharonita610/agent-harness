"""하이브리드 검색: 가짜 임베더로 RRF 융합과 폴백을 결정적으로 검증."""

import re

from harness.packs.enterprise.embeddings import EmbeddingCache, cosine
from harness.packs.enterprise.search import DocIndex
from harness.packs.enterprise.tools import DOCS_DIR


def bag_embedder(texts):
    """글자 2-gram 해시 백 벡터. 의미는 없지만 결정적이고 겹치는 글자가 많으면 유사도가 높다."""
    out = []
    for t in texts:
        v = [0.0] * 256
        s = re.sub(r"\s+", "", t)
        for i in range(len(s) - 1):
            v[hash(s[i : i + 2]) % 256] += 1.0
        out.append(v)
    return out


def test_bm25_only_when_no_embedder():
    idx = DocIndex(DOCS_DIR)
    assert idx.mode == "bm25"
    hits = idx.search("할인 승인 누가", k=3)
    assert hits[0]["doc"] == "할인 승인 규정" and hits[0]["mode"] == "bm25"


def test_hybrid_fuses_and_vector_mode_works(tmp_path):
    idx = DocIndex(DOCS_DIR, embedder=bag_embedder, embed_model="bag", cache_path=tmp_path / "emb.json")
    assert idx.mode == "hybrid" and idx.vectors is not None and len(idx.vectors) == len(idx.chunks)
    assert (tmp_path / "emb.json").exists()
    h = idx.search("지연 보상 승인자", k=3)
    assert h[0]["mode"] == "hybrid" and any(x["doc"] == "배송 지연 보상 규정" for x in h[:2])
    v = idx.search("배송 지연 보상 규정 승인자", k=3, mode="vector")
    assert v[0]["mode"] == "vector" and len(v) == 3
    b = idx.search("지연 보상 승인자", k=3, mode="bm25")
    assert b[0]["mode"] == "bm25"


def test_embedding_cache_reuses_vectors(tmp_path):
    calls = {"n": 0}

    def counting(texts):
        calls["n"] += len(texts)
        return bag_embedder(texts)

    cache = EmbeddingCache(tmp_path / "c.json")
    cache.get_many("bag", ["가나다", "라마바"], counting)
    cache2 = EmbeddingCache(tmp_path / "c.json")
    cache2.get_many("bag", ["가나다", "사아자"], counting)
    assert calls["n"] == 3  # 두 번째엔 새 텍스트 1개만 계산


def test_cosine():
    assert abs(cosine([1, 0], [1, 0]) - 1) < 1e-9 and abs(cosine([1, 0], [0, 1])) < 1e-9
