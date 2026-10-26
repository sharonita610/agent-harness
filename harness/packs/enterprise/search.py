"""규정 문서 검색. 마크다운을 제목 단위로 쪼개 SQLite FTS5(BM25) 로 색인한다.

한국어는 조사가 붙어 토큰이 달라지므로("보상은", "보상을") 접두 검색("보상"*) 으로 질의한다.
임베딩 기반 하이브리드 검색은 2주차 지식베이스 단계에서 추가한다.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


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
    def __init__(self, docs_dir: Path):
        self.docs_dir = Path(docs_dir)
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute("CREATE VIRTUAL TABLE chunks USING fts5(doc, section, text, tokenize='unicode61')")
        self.chunks: list[Chunk] = []
        for p in sorted(self.docs_dir.glob("*.md")):
            self.chunks.extend(chunk_markdown(p))
        self.conn.executemany("INSERT INTO chunks(doc, section, text) VALUES (?,?,?)", [(c.doc, c.section, c.text) for c in self.chunks])
        self.conn.commit()

    @staticmethod
    def build_query(query: str) -> str:
        words = [w for w in _WORD.findall(query) if len(w) >= 2]
        # 조사가 붙은 단어도 잡히게 접두 검색. 하나라도 맞으면 후보, BM25 로 순위.
        return " OR ".join(f'"{w}"*' for w in words) if words else '""'

    def search(self, query: str, k: int = 5) -> list[dict]:
        fts = self.build_query(query)
        rows = []
        if fts != '""':
            rows = self.conn.execute(
                "SELECT doc, section, text, bm25(chunks) AS score, snippet(chunks, 2, '[', ']', '…', 48) AS snip "
                "FROM chunks WHERE chunks MATCH ? ORDER BY score LIMIT ?",
                (fts, k),
            ).fetchall()
        if not rows:  # 접두 검색도 안 맞으면 부분 문자열로
            like = f"%{query.strip()}%"
            rows = self.conn.execute(
                "SELECT doc, section, text, 0.0, substr(text, 1, 160) FROM chunks WHERE text LIKE ? OR section LIKE ? LIMIT ?",
                (like, like, k),
            ).fetchall()
        return [
            {"doc": r[0], "section": r[1], "snippet": r[4], "text": r[2], "score": round(float(r[3]), 3), "source": f"{r[0]} > {r[1]}"}
            for r in rows
        ]

    def list_docs(self) -> list[dict]:
        out: dict[str, int] = {}
        for c in self.chunks:
            out[c.doc] = out.get(c.doc, 0) + 1
        return [{"doc": d, "sections": n} for d, n in out.items()]
