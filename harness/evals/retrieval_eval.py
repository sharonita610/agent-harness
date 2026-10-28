"""검색 품질 비교: bm25 / vector / hybrid 의 hit@1, hit@3, MRR. 임베딩 서버가 없으면 bm25 만.

enterprise: 규정 문서 10개(retrieval.yaml, 정답 = 문서 이름)
spice: 매뉴얼 지식 베이스(retrieval_spice.yaml, 정답 = 절 번호 또는 출처 문자열 조각). --index docs 면 요약 문서 6개만.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

from ..packs.enterprise.tools import DOCS_DIR
from ..retrieval import DocIndex, OpenAIEmbedder, embedder_available
from .runner import RESULTS_DIR

QUERIES = Path(__file__).parent / "retrieval.yaml"
QUERIES_SPICE = Path(__file__).parent / "retrieval_spice.yaml"


def _matches(item: dict, hit: dict) -> bool:
    """expect 항목: 절 번호('11.3.1'), 절 번호 접두('11.4*' → 11.4 와 11.4.x 전부), 문서 이름, 출처 문자열 조각."""
    expected = item.get("expect") or [item["doc"]]
    sid = hit.get("section_id") or ""
    src = hit.get("source") or ""
    for e in expected:
        if e.endswith("*"):
            base = e[:-1]
            if sid == base or sid.startswith(base + "."):
                return True
        elif e == sid or e == hit.get("doc") or (e in src and not e.replace(".", "").isdigit()):
            return True
    return False


def evaluate(index, queries: list[dict], mode: str, k: int = 3) -> dict:
    hit1 = hit3 = 0
    rr = 0.0
    rows = []
    for item in queries:
        hits = index.search(item["q"], k=k, mode=mode)
        rank = next((i + 1 for i, h in enumerate(hits) if _matches(item, h)), None)
        hit1 += rank == 1
        hit3 += rank is not None
        rr += 1 / rank if rank else 0
        rows.append(
            {
                "q": item["q"],
                "expected": item.get("expect") or item["doc"],
                "got": [h.get("source") or h["doc"] for h in hits],
                "rank": rank,
            }
        )
    n = len(queries)
    return {
        "mode": mode,
        "n": n,
        "hit@1": round(hit1 / n, 3),
        "hit@3": round(hit3 / n, 3),
        "mrr": round(rr / n, 3),
        "rows": rows,
    }


def run(console=None, pack: str = "enterprise", index_kind: str = "auto") -> dict:
    """index_kind: auto(spice 는 지식 베이스가 있으면 그것) | docs(마크다운 요약만) | kb"""
    emb = OpenAIEmbedder() if embedder_available() else None
    if pack == "spice":
        from ..packs.spice.tools import DOCS_DIR as SPICE_DOCS
        from ..packs.spice.tools import KB_PATH, make_index

        queries = yaml.safe_load(QUERIES_SPICE.read_text(encoding="utf-8"))
        if index_kind == "docs" or (index_kind == "auto" and not KB_PATH.exists()):
            index = (
                DocIndex(
                    SPICE_DOCS,
                    embedder=emb,
                    embed_model=emb.model if emb else None,
                    cache_path=RESULTS_DIR.parent.parent.parent / "data" / "spice_doc_embeddings.json",
                )
                if emb
                else DocIndex(SPICE_DOCS)
            )
            index_kind = "docs"
        else:
            index = make_index(hybrid=emb is not None, kb_path=KB_PATH)
            index_kind = "kb"
    else:
        queries = yaml.safe_load(QUERIES.read_text(encoding="utf-8"))
        index = (
            DocIndex(
                DOCS_DIR,
                embedder=emb,
                embed_model=emb.model,
                cache_path=RESULTS_DIR.parent.parent.parent / "data" / "doc_embeddings.json",
            )
            if emb
            else DocIndex(DOCS_DIR)
        )
        index_kind = "docs"
    modes = ["bm25"]
    if emb is not None and index.mode == "hybrid":
        modes += ["vector", "hybrid"]
    elif emb is not None and console:
        console.print(
            f"[yellow]임베딩 {emb.model} 이 지식 베이스에 없어 bm25 만 잽니다. 먼저: uv run harness kb embed --model …[/yellow]"
        )
    results = [evaluate(index, queries, m) for m in modes]
    embed_model = emb.model if emb else None
    for r in results:
        r["embed_model"] = embed_model if r["mode"] != "bm25" else None
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = (
        RESULTS_DIR
        / f"retrieval_{datetime.now().strftime('%Y%m%d-%H%M%S')}{('_' + pack + '-' + index_kind) if pack != 'enterprise' else ''}.json"
    )
    out.write_text(
        json.dumps(
            {
                "pack": pack,
                "index": index_kind,
                "queries": len(queries),
                "embed_model": embed_model,
                "embed_base_url": emb.base_url if emb else None,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    table = ["| 모드 | hit@1 | hit@3 | MRR |", "|---|---|---|---|"] + [
        f"| {r['mode']}{(' (' + r['embed_model'] + ')') if r.get('embed_model') else ''} | {r['hit@1']:.0%} | {r['hit@3']:.0%} | {r['mrr']:.2f} |"
        for r in results
    ]
    if console:
        console.print("\n".join(table))
        for r in results:
            misses = [x for x in r["rows"] if x["rank"] != 1]
            if misses:
                console.print(
                    f"[dim]{r['mode']} 1위 놓친 질문 {len(misses)}개: "
                    + "; ".join(f"{m['q']} → {m['got'][0] if m['got'] else '-'}" for m in misses[:6])
                    + "[/dim]"
                )
        console.print(f"[dim]저장: {out}[/dim]")
    return {"table": "\n".join(table), "results": results, "path": str(out)}
