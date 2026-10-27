"""검색 품질 비교: bm25 / vector / hybrid 의 hit@1, hit@3, MRR. 임베딩 서버가 없으면 bm25 만."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

from ..retrieval import OpenAIEmbedder, embedder_available
from ..retrieval import DocIndex
from ..packs.enterprise.tools import DOCS_DIR
from .runner import RESULTS_DIR

QUERIES = Path(__file__).parent / "retrieval.yaml"


def evaluate(index: DocIndex, queries: list[dict], mode: str, k: int = 3) -> dict:
    hit1 = hit3 = 0
    rr = 0.0
    rows = []
    for item in queries:
        hits = index.search(item["q"], k=k, mode=mode)
        docs = [h["doc"] for h in hits]
        rank = docs.index(item["doc"]) + 1 if item["doc"] in docs else None
        hit1 += rank == 1
        hit3 += rank is not None
        rr += 1 / rank if rank else 0
        rows.append({"q": item["q"], "expected": item["doc"], "got": docs, "rank": rank})
    n = len(queries)
    return {"mode": mode, "n": n, "hit@1": round(hit1 / n, 3), "hit@3": round(hit3 / n, 3), "mrr": round(rr / n, 3), "rows": rows}


def run(console=None) -> dict:
    queries = yaml.safe_load(QUERIES.read_text(encoding="utf-8"))
    modes = ["bm25"]
    if embedder_available():
        emb = OpenAIEmbedder()
        index = DocIndex(DOCS_DIR, embedder=emb, embed_model=emb.model, cache_path=RESULTS_DIR.parent.parent.parent / "data" / "doc_embeddings.json")
        modes += ["vector", "hybrid"]
    else:
        index = DocIndex(DOCS_DIR)
    results = [evaluate(index, queries, m) for m in modes]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"retrieval_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"queries": len(queries), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    table = ["| 모드 | hit@1 | hit@3 | MRR |", "|---|---|---|---|"] + [f"| {r['mode']} | {r['hit@1']:.0%} | {r['hit@3']:.0%} | {r['mrr']:.2f} |" for r in results]
    if console:
        console.print("\n".join(table))
        for r in results:
            misses = [x for x in r["rows"] if x["rank"] != 1]
            if misses:
                console.print(f"[dim]{r['mode']} 1위 놓친 질문 {len(misses)}개: " + "; ".join(f"{m['q']} → {m['got'][0] if m['got'] else '-'}" for m in misses[:6]) + "[/dim]")
        console.print(f"[dim]저장: {out}[/dim]")
    return {"table": "\n".join(table), "results": results, "path": str(out)}
