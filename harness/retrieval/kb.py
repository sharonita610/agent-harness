"""지식 베이스: PDF 매뉴얼(수백 쪽)과 마크다운 문서를 절 단위 청크로 잘라 SQLite 에 넣고 BM25 + 벡터로 검색한다.

DocIndex(메모리, 문서 몇 개)와 같은 검색 인터페이스(search / list_docs / mode)를 지키되,
- 원본이 커서 디스크(SQLite: chunks + FTS5 + embeddings)에 둔다
- 임베딩은 (모델, 청크 해시) 단위로 증분 계산한다 → 문서가 바뀌면 바뀐 청크만 다시 계산
- 벡터 검색은 numpy 행렬 곱(수천 청크 × 768~1536 차원, 수 ms)
PDF 청킹은 책갈피(TOC)의 절 번호를 본문 블록에서 찾아 경계로 쓴다. 머리글·쪽번호는 위치로 버린다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from .embeddings import Embedder
from .search import RRF_K, DocIndex, chunk_markdown

MAX_CHUNK_CHARS = 1800
HEADER_Y, FOOTER_Y = 42.0, 790.0   # 이 매뉴얼(A4, LaTeX)의 머리글·쪽번호 위치
_SECTION_NO = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)$")
_CHAPTER = re.compile(r"^(\d+)\s+(.+)$")


@dataclass
class KBChunk:
    doc: str
    section_id: str      # "15.4.3" 또는 마크다운이면 ""
    section: str         # 절 제목
    title_path: str      # "2 Circuit Description > 2.1 … > 2.1.1 …"
    page_start: int
    page_end: int
    part: int            # 긴 절을 나눈 조각 번호(0부터)
    text: str

    @property
    def source(self) -> str:
        if self.section_id:
            return f"{self.doc} > {self.section_id} {self.section} (p.{self.page_start})"
        return f"{self.doc} > {self.section}"

    @property
    def hash(self) -> str:
        return hashlib.sha1(f"{self.doc}|{self.section_id}|{self.section}|{self.part}|{self.text}".encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------- 수집
def _split_long(text: str, max_chars: int) -> list[str]:
    """문단(빈 줄) 경계에서 max_chars 안쪽으로 나눈다. 한 문단이 그보다 길면 줄 단위로."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    buf = ""
    for para in re.split(r"\n{2,}", text):
        if len(para) > max_chars:
            for line in para.split("\n"):
                if len(buf) + len(line) + 1 > max_chars and buf:
                    parts.append(buf.strip()); buf = ""
                buf += line + "\n"
            continue
        if len(buf) + len(para) + 2 > max_chars and buf:
            parts.append(buf.strip()); buf = ""
        buf += para + "\n\n"
    if buf.strip():
        parts.append(buf.strip())
    return parts


def ingest_pdf(pdf_path: Path, doc_name: str = "ngspice manual", max_chars: int = MAX_CHUNK_CHARS, min_level: int = 2) -> list[KBChunk]:
    """책갈피(TOC) 절 번호로 본문을 절 단위로 자른다. 레벨 1(부, Part)은 건너뛴다."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    toc = [(lvl, title.strip(), page) for lvl, title, page in doc.get_toc() if lvl >= min_level]
    entries = []
    for lvl, title, page in toc:
        m = _SECTION_NO.match(title)
        if not m:
            continue  # 번호 없는 항목(부록 머리 등)은 상위 절에 흡수
        entries.append({"level": lvl, "no": m.group(1), "title": " ".join(m.group(2).split()), "page": page})
    # 제목 경로
    stack: list[tuple[int, str]] = []
    for e in entries:
        while stack and stack[-1][0] >= e["level"]:
            stack.pop()
        stack.append((e["level"], f"{e['no']} {e['title']}"))
        e["path"] = " > ".join(t for _, t in stack)

    by_page: dict[int, list[dict]] = {}
    for e in entries:
        by_page.setdefault(e["page"], []).append(e)

    sections: list[dict] = []
    current: dict | None = None
    first_page = entries[0]["page"] if entries else 1
    for pno in range(first_page, doc.page_count + 1):
        page = doc[pno - 1]
        pending = list(by_page.get(pno, []))
        for x0, y0, x1, y1, text, _bno, _bt in page.get_text("blocks"):
            if y1 < HEADER_Y or y0 > FOOTER_Y:
                continue  # 머리글(절 제목 + 쪽번호), 바닥 쪽번호
            text = text.strip()
            if not text or re.fullmatch(r"\d{1,4}", text):
                continue
            first_line = text.split("\n", 1)[0].strip()
            started = None
            for e in pending:
                if first_line == e["no"] or first_line.lower() == f"{e['no']} {e['title']}".lower()[:len(first_line)] and first_line.startswith(e["no"] + " "):
                    started = e
                    break
            if started is None and pending and re.fullmatch(r"Chapter \d+", first_line):
                started = next((e for e in pending if e["level"] == min_level and e["no"] == first_line.split()[1]), None)
                if started:
                    text = ""  # "Chapter 2" / 제목 블록은 본문이 아니다
            if started is not None:
                pending.remove(started)
                current = {**started, "page_end": pno, "blocks": []}
                sections.append(current)
                rest = text.split("\n", 1)[1].strip() if "\n" in text and first_line in (started["no"], f"{started['no']} {started['title']}") else text
                if rest and rest.lower() != started["title"].lower():
                    current["blocks"].append(rest)
                continue
            if current is None:
                continue
            if not text or (current["level"] == min_level and current["page"] == pno and text.lower() == current["title"].lower()):
                continue
            current["blocks"].append(text)
            current["page_end"] = pno
        # 이 쪽에서 못 찾은 항목은 쪽 끝에서 시작한 것으로 본다(형식이 다른 머리말 등)
        for e in pending:
            current = {**e, "page_end": pno, "blocks": []}
            sections.append(current)

    chunks: list[KBChunk] = []
    for s in sections:
        body = "\n".join(s["blocks"]).strip()
        if not body:
            continue
        for i, part in enumerate(_split_long(body, max_chars)):
            chunks.append(KBChunk(doc_name, s["no"], s["title"], s["path"], s["page"], s["page_end"], i, part))
    return chunks


def ingest_markdown_dir(docs_dir: Path, max_chars: int = MAX_CHUNK_CHARS) -> list[KBChunk]:
    out: list[KBChunk] = []
    for p in sorted(Path(docs_dir).glob("*.md")):
        for c in chunk_markdown(p):
            for i, part in enumerate(_split_long(c.text, max_chars)):
                out.append(KBChunk(c.doc, "", c.section, f"{c.doc} > {c.section}", 0, 0, i, part))
    return out


# ----------------------------------------------------------------------------- 저장소 + 검색
_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(name TEXT PRIMARY KEY, kind TEXT, path TEXT, chunks INTEGER, built_at TEXT);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, doc TEXT, section_id TEXT, section TEXT, title_path TEXT,
                                  page_start INTEGER, page_end INTEGER, part INTEGER, text TEXT, hash TEXT);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc);
CREATE INDEX IF NOT EXISTS chunks_hash ON chunks(hash);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, section, title_path, content='chunks', content_rowid='id', tokenize='unicode61');
CREATE TABLE IF NOT EXISTS embeddings(hash TEXT, model TEXT, dim INTEGER, vec BLOB, PRIMARY KEY(hash, model));
"""


def _pack(v: Sequence[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


class KnowledgeBase:
    """DocIndex 와 같은 search(query, k, mode) / list_docs() / mode 를 제공한다."""

    def __init__(self, path: Path | str, embedder: Embedder | None = None, embed_model: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.embedder = embedder
        self.embed_model = embed_model
        self._matrix: dict[str, tuple[list[int], Any]] = {}

    # ---- 구축
    def build(self, chunks: list[KBChunk], kind: str = "pdf", path: str = "") -> dict[str, int]:
        """문서 단위로 교체한다. 같은 해시의 임베딩은 남아 있어 다시 계산하지 않는다."""
        docs = sorted({c.doc for c in chunks})
        with self.conn:
            for d in docs:
                self.conn.execute("DELETE FROM chunks WHERE doc=?", (d,))
            self.conn.executemany(
                "INSERT INTO chunks(doc, section_id, section, title_path, page_start, page_end, part, text, hash) VALUES (?,?,?,?,?,?,?,?,?)",
                [(c.doc, c.section_id, c.section, c.title_path, c.page_start, c.page_end, c.part, c.text, c.hash) for c in chunks])
            for d in docs:
                n = self.conn.execute("SELECT COUNT(*) FROM chunks WHERE doc=?", (d,)).fetchone()[0]
                self.conn.execute("INSERT OR REPLACE INTO docs(name, kind, path, chunks, built_at) VALUES (?,?,?,?,?)",
                                  (d, kind, path, n, datetime.now().isoformat(timespec="seconds")))
            self.conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        self._matrix.clear()
        return {d: self.conn.execute("SELECT COUNT(*) FROM chunks WHERE doc=?", (d,)).fetchone()[0] for d in docs}

    def embed(self, model: str, embedder: Embedder, batch: int = 32, on_progress: Callable[[int, int], None] | None = None) -> int:
        """model 로 아직 임베딩이 없는 청크만 계산해 저장한다(증분). 계산한 개수를 돌려준다."""
        rows = self.conn.execute(
            "SELECT DISTINCT c.hash, c.title_path, c.text FROM chunks c LEFT JOIN embeddings e ON e.hash=c.hash AND e.model=? WHERE e.hash IS NULL",
            (model,)).fetchall()
        done = 0
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            vecs = embedder([f"{r['title_path']}\n{r['text']}" for r in part])
            with self.conn:
                self.conn.executemany("INSERT OR REPLACE INTO embeddings(hash, model, dim, vec) VALUES (?,?,?,?)",
                                      [(r["hash"], model, len(v), _pack(v)) for r, v in zip(part, vecs)])
            done += len(part)
            if on_progress:
                on_progress(done, len(rows))
        self._matrix.pop(model, None)
        return done

    def coverage(self, model: str | None) -> float:
        if not model:
            return 0.0
        total = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if not total:
            return 0.0
        have = self.conn.execute("SELECT COUNT(*) FROM chunks c JOIN embeddings e ON e.hash=c.hash AND e.model=?", (model,)).fetchone()[0]
        return have / total

    def stats(self) -> dict[str, Any]:
        docs = [dict(r) for r in self.conn.execute("SELECT name, kind, chunks, built_at FROM docs ORDER BY name")]
        models = [{"model": r[0], "chunks": r[1], "dim": r[2]} for r in self.conn.execute("SELECT model, COUNT(*), MAX(dim) FROM embeddings GROUP BY model")]
        return {"path": str(self.path), "chunks": self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0], "docs": docs, "embeddings": models}

    # ---- 검색
    @property
    def mode(self) -> str:
        return "hybrid" if self.embedder is not None and self.coverage(self.embed_model) >= 0.95 else "bm25"

    def _bm25_ranked(self, query: str, limit: int) -> list[tuple[int, float, str]]:
        fts = DocIndex.build_query(query)
        rows = []
        if fts != '""':
            rows = self.conn.execute(
                "SELECT rowid, bm25(chunks_fts, 1.0, 0.5, 0.5) AS score, snippet(chunks_fts, 0, '[', ']', '…', 40) AS snip "
                "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?", (fts, limit)).fetchall()
        if not rows:
            like = f"%{query.strip()}%"
            rows = self.conn.execute("SELECT id, 0.0, substr(text, 1, 160) FROM chunks WHERE text LIKE ? OR section LIKE ? LIMIT ?",
                                     (like, like, limit)).fetchall()
        return [(int(r[0]), float(r[1]), r[2]) for r in rows]

    def _load_matrix(self, model: str):
        import numpy as np

        if model not in self._matrix:
            rows = self.conn.execute("SELECT c.id, e.vec, e.dim FROM chunks c JOIN embeddings e ON e.hash=c.hash AND e.model=? ORDER BY c.id", (model,)).fetchall()
            ids = [int(r[0]) for r in rows]
            mat = np.array([struct.unpack(f"{r[2]}f", r[1]) for r in rows], dtype=np.float32) if rows else np.zeros((0, 1), dtype=np.float32)
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1
            self._matrix[model] = (ids, mat / norms)
        return self._matrix[model]

    def _vector_ranked(self, query: str, limit: int) -> list[tuple[int, float]]:
        import numpy as np

        if self.embedder is None or not self.embed_model:
            return []
        ids, mat = self._load_matrix(self.embed_model)
        if not ids:
            return []
        q = np.array(self.embedder([query])[0], dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        scores = mat @ q
        top = np.argsort(-scores)[:limit]
        return [(ids[i], float(scores[i])) for i in top]

    def search(self, query: str, k: int = 5, mode: str | None = None) -> list[dict]:
        mode = mode or self.mode
        pool = max(k * 4, 10)
        bm = self._bm25_ranked(query, pool)
        snippets = {i: snip for i, _, snip in bm}
        if mode == "bm25" or self.embedder is None:
            ranked = [(i, score) for i, score, _ in bm][:k]
            how = "bm25"
        else:
            vec = self._vector_ranked(query, pool)
            if mode == "vector":
                ranked, how = vec[:k], "vector"
            else:
                fused: dict[int, float] = {}
                for rank, (i, _, _) in enumerate(bm):
                    fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
                for rank, (i, _) in enumerate(vec):
                    fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
                ranked, how = sorted(fused.items(), key=lambda x: -x[1])[:k], "hybrid"
        out = []
        for i, score in ranked:
            r = self.conn.execute("SELECT * FROM chunks WHERE id=?", (i,)).fetchone()
            if r is None:
                continue
            c = KBChunk(r["doc"], r["section_id"], r["section"], r["title_path"], r["page_start"], r["page_end"], r["part"], r["text"])
            out.append({"doc": c.doc, "section": c.section, "section_id": c.section_id, "page": c.page_start or None, "part": c.part,
                        "snippet": snippets.get(i) or c.text[:160], "text": c.text, "score": round(float(score), 4), "source": c.source, "mode": how})
        return out

    def get_section(self, ref: str, max_chars: int = 4000) -> dict | None:
        """절 번호('15.4.3') 또는 출처 문자열('문서 > 절')로 절 전체를 읽는다(여러 조각이면 이어 붙임)."""
        ref = ref.strip()
        rows = self.conn.execute("SELECT * FROM chunks WHERE section_id=? ORDER BY part", (ref,)).fetchall()
        if not rows:
            like = f"%{ref.split('>')[-1].strip()}%"
            rows = self.conn.execute("SELECT * FROM chunks WHERE section LIKE ? OR title_path LIKE ? ORDER BY doc, section_id, part LIMIT 6", (like, like)).fetchall()
            if rows:
                key = (rows[0]["doc"], rows[0]["section_id"], rows[0]["section"])
                rows = [r for r in rows if (r["doc"], r["section_id"], r["section"]) == key]
        if not rows:
            return None
        text = "\n\n".join(r["text"] for r in rows)
        c = KBChunk(rows[0]["doc"], rows[0]["section_id"], rows[0]["section"], rows[0]["title_path"], rows[0]["page_start"], rows[-1]["page_end"], 0, text)
        return {"source": c.source, "title_path": c.title_path, "page_start": c.page_start, "page_end": c.page_end,
                "text": text[:max_chars] + ("\n…(잘림)" if len(text) > max_chars else ""), "chars": len(text)}

    def list_docs(self) -> list[dict]:
        return [{"doc": r["name"], "kind": r["kind"], "chunks": r["chunks"]} for r in self.conn.execute("SELECT * FROM docs ORDER BY name")]
