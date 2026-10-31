"""규정 문서 검색: BM25(FTS5) + 임베딩 하이브리드.

- BM25: 마크다운을 제목 단위로 쪼개 SQLite FTS5 로 색인. 한국어는 조사가 붙어 토큰이 달라지므로("보상은", "보상을") 접두 검색("보상"*).
- 임베딩: 임베딩 서버(OpenAI 호환 /v1/embeddings)가 있으면 청크 벡터를 만들어 코사인 유사도로 순위를 내고,
  두 순위를 RRF(Reciprocal Rank Fusion) 로 합친다. 서버가 없으면 BM25 만 쓴다(동작은 같고 품질만 다름).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .embeddings import Embedder, EmbeddingCache, cosine

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")
RRF_K = 60  # RRF 상수. 작을수록 1위 근처의 순위 차가 크게 반영된다(retrieval-eval --rrf-k 로 실험, ADR-031)


def split_long(text: str, max_chars: int) -> list[str]:
    """문단(빈 줄) 경계에서 max_chars 안쪽으로 나눈다. 한 문단이 그보다 길면 줄 단위로. max_chars 가 0 이하면 자르지 않는다."""
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    buf = ""
    for para in re.split(r"\n{2,}", text):
        if len(para) > max_chars:
            for line in para.split("\n"):
                if len(buf) + len(line) + 1 > max_chars and buf:
                    parts.append(buf.strip())
                    buf = ""
                buf += line + "\n"
            continue
        if len(buf) + len(para) + 2 > max_chars and buf:
            parts.append(buf.strip())
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        parts.append(buf.strip())
    return parts


def rrf_fuse(
    bm25_ids: list[int],
    vector_ids: list[int],
    rrf_k: int | None = None,
    bm25_weight: float = 1.0,
    vector_weight: float = 1.0,
) -> list[tuple[int, float]]:
    """두 순위 목록을 RRF(Reciprocal Rank Fusion)로 합친다: 점수 = Σ 가중치 / (k + 순위). 기본은 k=60, 가중치 1:1."""
    k = RRF_K if rrf_k is None else rrf_k
    fused: dict[int, float] = {}
    for rank, i in enumerate(bm25_ids):
        fused[i] = fused.get(i, 0.0) + bm25_weight / (k + rank + 1)
    for rank, i in enumerate(vector_ids):
        fused[i] = fused.get(i, 0.0) + vector_weight / (k + rank + 1)
    return sorted(fused.items(), key=lambda x: -x[1])


@dataclass
class Chunk:
    doc: str
    section: str
    text: str


def chunk_markdown(path: Path) -> list[Chunk]:
    title = path.stem
    section = "(서문)"
    buf: list[str] = []
    chunks: list[Chunk] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            if "".join(buf).strip():
                chunks.append(Chunk(title, section, "\n".join(buf).strip()))
            section = line[3:].strip()
            buf = []
            continue
        buf.append(line)
    if "".join(buf).strip():
        chunks.append(Chunk(title, section, "\n".join(buf).strip()))
    return chunks


class DocIndex:
    def __init__(
        self,
        docs_dir: Path,
        embedder: Embedder | None = None,
        embed_model: str = "local-embed",
        cache_path: Path | None = None,
        max_chars: int | None = None,
    ):
        """max_chars 를 주면 절 하나가 그보다 길 때 문단 경계에서 나눈다(기본은 절 그대로, 청크 크기 실험용)."""
        self.docs_dir = Path(docs_dir)
        self.max_chars = max_chars
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute("CREATE VIRTUAL TABLE chunks USING fts5(doc, section, text, tokenize='unicode61')")
        self.chunks: list[Chunk] = []
        for p in sorted(self.docs_dir.glob("*.md")):
            for c in chunk_markdown(p):
                if max_chars:
                    self.chunks.extend(Chunk(c.doc, c.section, part) for part in split_long(c.text, max_chars))
                else:
                    self.chunks.append(c)
        self.conn.executemany(
            "INSERT INTO chunks(doc, section, text) VALUES (?,?,?)", [(c.doc, c.section, c.text) for c in self.chunks]
        )
        self.conn.commit()
        # 임베딩(선택). 청크 수가 작아 전부 메모리에 둔다.
        self.embedder = embedder
        self.embed_model = embed_model
        self.vectors: list[list[float]] | None = None
        if embedder is not None:
            cache = EmbeddingCache(cache_path)
            texts = [f"{c.doc} > {c.section}\n{c.text}" for c in self.chunks]
            self.vectors = cache.get_many(embed_model, texts, embedder)

    @property
    def mode(self) -> str:
        return "hybrid" if self.vectors is not None else "bm25"

    @staticmethod
    def build_query(query: str) -> str:
        words = [w for w in _WORD.findall(query) if len(w) >= 2]
        # 조사가 붙은 단어도 잡히게 접두 검색. 하나라도 맞으면 후보, BM25 로 순위.
        return " OR ".join(f'"{w}"*' for w in words) if words else '""'

    def _bm25_ranked(self, query: str, limit: int) -> list[tuple[int, float, str]]:
        """(청크 인덱스, bm25 점수, 스니펫) 목록. 접두 검색이 비면 부분 문자열로."""
        fts = self.build_query(query)
        rows = []
        if fts != '""':
            rows = self.conn.execute(
                "SELECT rowid, bm25(chunks) AS score, snippet(chunks, 2, '[', ']', '…', 48) AS snip "
                "FROM chunks WHERE chunks MATCH ? ORDER BY score LIMIT ?",
                (fts, limit),
            ).fetchall()
        if not rows:
            like = f"%{query.strip()}%"
            rows = self.conn.execute(
                "SELECT rowid, 0.0, substr(text, 1, 160) FROM chunks WHERE text LIKE ? OR section LIKE ? LIMIT ?",
                (like, like, limit),
            ).fetchall()
        return [(int(r[0]) - 1, float(r[1]), r[2]) for r in rows]

    def _vector_ranked(self, query: str, limit: int) -> list[tuple[int, float]]:
        if self.vectors is None or self.embedder is None:
            return []
        qv = self.embedder([query])[0]
        sims = [(i, cosine(qv, v)) for i, v in enumerate(self.vectors)]
        sims.sort(key=lambda x: -x[1])
        return sims[:limit]

    def search(
        self,
        query: str,
        k: int = 5,
        mode: str | None = None,
        rrf_k: int | None = None,
        bm25_weight: float = 1.0,
        vector_weight: float = 1.0,
    ) -> list[dict]:
        """mode: None(가능하면 hybrid) | 'bm25' | 'vector' | 'hybrid'. rrf_k·가중치는 hybrid 융합에만 쓴다(기본 60, 1:1)."""
        mode = mode or self.mode
        pool = max(k * 4, 10)
        bm = self._bm25_ranked(query, pool)
        snippets = {i: snip for i, _, snip in bm}
        if mode == "bm25" or self.vectors is None:
            ranked = [(i, score) for i, score, _ in bm][:k]
            how = "bm25"
        else:
            vec = self._vector_ranked(query, pool)
            if mode == "vector":
                ranked = vec[:k]
                how = "vector"
            else:  # RRF
                ranked = rrf_fuse([i for i, _, _ in bm], [i for i, _ in vec], rrf_k, bm25_weight, vector_weight)[:k]
                how = "hybrid"
        out = []
        for i, score in ranked:
            c = self.chunks[i]
            out.append(
                {
                    "doc": c.doc,
                    "section": c.section,
                    "snippet": snippets.get(i) or c.text[:160],
                    "text": c.text,
                    "score": round(float(score), 4),
                    "source": f"{c.doc} > {c.section}",
                    "mode": how,
                }
            )
        return out

    def list_docs(self) -> list[dict]:
        out: dict[str, int] = {}
        for c in self.chunks:
            out[c.doc] = out.get(c.doc, 0) + 1
        return [{"doc": d, "sections": n} for d, n in out.items()]
