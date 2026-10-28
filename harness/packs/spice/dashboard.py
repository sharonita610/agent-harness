"""회로 비서의 "데이터" 탭: 회로 목록, 최근 실행과 측정값, 실험 추적 그래프 그림."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .graph import GRAPH_PATH, ExperimentGraph, render_graph_svg
from .netlist import format_si
from .simulator import RUNS_DIR
from .tools import CIRCUITS_DIR, LIBRARY_DIR, _circuit_meta, _unit_for


def _recent_runs(runs_dir: Path, limit: int = 10) -> list[dict[str, Any]]:
    metas = sorted(runs_dir.glob("*/meta.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for m in metas:
        try:
            d = json.loads(m.read_text(encoding="utf-8"))
        except Exception:
            continue
        meas = d.get("measurements") or {}
        kind = d.get("analysis_kind", "")
        pretty = ", ".join(f"{k}={format_si(v, _unit_for(k, kind)) if isinstance(v, (int, float)) else v}" for k, v in list(meas.items())[:3])
        out.append({"실행": d.get("run_id", m.parent.name), "회로": (d.get("title") or "").lstrip("* ")[:28], "해석": d.get("analysis", ""), "점 수": d.get("points", ""),
                    "측정값": pretty, "소요(ms)": d.get("elapsed_ms", "")})
    return out


def dashboard(scope: Any = None, runs_dir: Path = RUNS_DIR, circuits_dir: Path = CIRCUITS_DIR, library_dir: Path = LIBRARY_DIR, graph_path: Path = GRAPH_PATH) -> list[dict[str, Any]]:
    samples = [_circuit_meta(p, "sample") for p in sorted(circuits_dir.glob("*.cir"))]
    saved = [_circuit_meta(p, "saved") for p in sorted(library_dir.glob("*.cir"))] if library_dir.exists() else []
    runs = _recent_runs(runs_dir)
    kinds = Counter()
    for m in runs_dir.glob("*/meta.json"):
        try:
            kinds[json.loads(m.read_text(encoding="utf-8")).get("analysis_kind", "?")] += 1
        except Exception:
            pass
    panels: list[dict[str, Any]] = [
        {"id": "stats", "title": "한눈에", "kind": "stats", "span": 2, "data": [
            {"label": "샘플 회로", "value": len(samples), "unit": "개"},
            {"label": "저장한 회로", "value": len(saved), "unit": "개"},
            {"label": "실행 기록", "value": sum(kinds.values()), "unit": "건"},
        ]},
        {"id": "circuits", "title": "회로 목록", "kind": "table", "data": {"columns": ["이름", "설명", "출처", "해석", "소자 수"], "rows": [
            {"이름": c["name"], "설명": c["description"] or c["title"], "출처": "샘플" if c["source"] == "sample" else "저장됨", "해석": ", ".join(c["analyses"]), "소자 수": c["components"]} for c in samples + saved]}},
        {"id": "kinds", "title": "해석 종류별 실행 수", "kind": "bar", "data": {"x": list(kinds.keys()), "series": [{"name": "실행", "values": list(kinds.values())}], "unit": "건"}},
        {"id": "runs", "title": "최근 실행", "kind": "table", "span": 2, "data": {"columns": ["실행", "회로", "해석", "점 수", "측정값", "소요(ms)"], "rows": runs}},
    ]
    try:
        g = ExperimentGraph(graph_path)
        if g.stats()["nodes"].get("run"):
            svg = render_graph_svg(g)
            (runs_dir / "graph.svg").write_text(svg, encoding="utf-8")
            panels.append({"id": "graph", "title": "실험 추적 그래프 (회로 → 실행 → 측정값)", "kind": "image", "span": 2, "data": {"url": "/files/spice/graph.svg", "alt": "실험 추적 그래프"},
                           "note": "같은 토폴로지에서 값만 다른 변형은 점선으로 이어진다. 비서의 query_history·compare_runs 가 보는 것과 같은 기록이다."})
    except Exception:
        pass
    return panels
