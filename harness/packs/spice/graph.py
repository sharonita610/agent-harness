"""실험 추적 그래프: 시뮬레이션 실행을 회로 → 소자 → 해석 → 실행 → 측정 노드와 관계로 기록한다.

왜 그래프인가: "지난번 R1 을 2k 로 했을 때 fc 얼마였지", "같은 회로로 돌린 실행 전부", "이 실행과 소자 하나만 다른 실행" 은
파형 파일이나 로그를 뒤져서는 답할 수 없고, 실행 사이의 *관계*(같은 토폴로지, 값 하나 차이, 어떤 측정이 나왔나)를 저장해야 답할 수 있다.
저장은 SQLite 두 테이블(nodes, edges). 그래프 DB 없이도 수천 실행까지는 충분하다.

노드: circuit(넷리스트 본문 해시) · component(회로/소자) · analysis · run(sim | sweep | sweep_point) · measurement
관계: run -simulates-> circuit · circuit -has_component-> component · circuit -has_analysis-> analysis · run -measured-> measurement
      sweep -has_point-> run(point) · sweep -varies-> component · circuit -variant_of-> circuit(같은 토폴로지, 값만 다름, diff 를 관계에 저장)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .netlist import Netlist, NetlistError, parse_si

ROOT = Path(__file__).resolve().parents[3]
GRAPH_PATH = Path(os.getenv("HARNESS_SPICE_GRAPH", str(ROOT / "data" / "spice_runs" / "experiments.sqlite")))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT, props TEXT NOT NULL DEFAULT '{}', created_at TEXT);
CREATE INDEX IF NOT EXISTS nodes_type ON nodes(type);
CREATE TABLE IF NOT EXISTS edges(src TEXT NOT NULL, rel TEXT NOT NULL, dst TEXT NOT NULL, props TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(src, rel, dst));
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, rel);
"""


def _h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _value_si(c) -> float | None:
    if c.value is None or c.kind not in "RLCVI":
        return None
    try:
        return parse_si(c.value)
    except NetlistError:
        return None


class ExperimentGraph:
    def __init__(self, path: Path | str = GRAPH_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    # ----------------------------------------------------------------- 기본 연산
    def clear(self) -> None:
        """모든 노드·관계를 지운다. 파일을 지우지 않으므로 같은 파일을 연 다른 연결(도구 레지스트리)이 계속 유효하다."""
        with self.conn:
            self.conn.execute("DELETE FROM edges")
            self.conn.execute("DELETE FROM nodes")

    def put_node(self, nid: str, ntype: str, label: str, props: dict[str, Any] | None = None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO nodes(id, type, label, props, created_at) VALUES (?,?,?,?,COALESCE((SELECT created_at FROM nodes WHERE id=?), ?))",
                          (nid, ntype, label, json.dumps(props or {}, ensure_ascii=False), nid, datetime.now().isoformat(timespec="seconds")))

    def put_edge(self, src: str, rel: str, dst: str, props: dict[str, Any] | None = None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO edges(src, rel, dst, props) VALUES (?,?,?,?)", (src, rel, dst, json.dumps(props or {}, ensure_ascii=False)))

    def node(self, nid: str) -> dict[str, Any] | None:
        r = self.conn.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
        return {"id": r["id"], "type": r["type"], "label": r["label"], "created_at": r["created_at"], **json.loads(r["props"])} if r else None

    def out(self, nid: str, rel: str | None = None) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
        q = "SELECT rel, dst, props FROM edges WHERE src=?" + (" AND rel=?" if rel else "")
        rows = self.conn.execute(q, (nid, rel) if rel else (nid,)).fetchall()
        return [(r["rel"], self.node(r["dst"]) or {"id": r["dst"]}, json.loads(r["props"])) for r in rows]

    def inbound(self, nid: str, rel: str | None = None) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
        q = "SELECT rel, src, props FROM edges WHERE dst=?" + (" AND rel=?" if rel else "")
        rows = self.conn.execute(q, (nid, rel) if rel else (nid,)).fetchall()
        return [(r["rel"], self.node(r["src"]) or {"id": r["src"]}, json.loads(r["props"])) for r in rows]

    # ----------------------------------------------------------------- 기록
    def _circuit_id(self, nl: Netlist) -> tuple[str, str]:
        body = "\n".join(ln for ln in nl.lines if not ln.startswith("*"))
        topo = "|".join(f"{c.name}:{c.kind}:{','.join(c.nodes)}" for c in nl.components)
        return "circuit:" + _h(body), "topology:" + _h(topo)

    def record_circuit(self, nl: Netlist) -> str:
        cid, topo = self._circuit_id(nl)
        if self.node(cid):
            return cid
        comps = {c.name: {"kind": c.kind, "nodes": c.nodes, "value": c.value, "value_si": _value_si(c)} for c in nl.components}
        self.put_node(cid, "circuit", nl.title.lstrip("* ").strip() or "circuit", {"topology": topo, "n_components": len(comps), "analyses": nl.analyses, "components": comps})
        for name, c in comps.items():
            comp_id = f"{cid}/{name}"
            self.put_node(comp_id, "component", name, c)
            self.put_edge(cid, "has_component", comp_id)
        for a in nl.analyses:
            aid = f"{cid}/analysis:{_h(a)}"
            self.put_node(aid, "analysis", a, {"kind": a.split()[0][1:].lower(), "line": a})
            self.put_edge(cid, "has_analysis", aid)
        # 같은 토폴로지의 기존 회로와 잇는다(값 차이를 관계에 적는다)
        for r in self.conn.execute("SELECT id, props FROM nodes WHERE type='circuit' AND id!=?", (cid,)):
            other = json.loads(r["props"])
            if other.get("topology") != topo:
                continue
            # 관계의 diffs 는 src 회로 값 → dst 회로 값 방향이다
            diffs = [{"component": n, "from": comps[n].get("value"), "to": other["components"][n].get("value")}
                     for n in comps if n in other["components"] and other["components"][n].get("value") != comps[n].get("value")]
            self.put_edge(cid, "variant_of", r["id"], {"diffs": diffs, "n_diffs": len(diffs)})
            self.put_edge(r["id"], "variant_of", cid, {"diffs": [{"component": d["component"], "from": d["to"], "to": d["from"]} for d in diffs], "n_diffs": len(diffs)})
        self.conn.commit()
        return cid

    def record_simulation(self, nl: Netlist, result: dict[str, Any], question: str | None = None, kind: str = "sim",
                          overrides: dict[str, float] | None = None, parent: str | None = None) -> dict[str, Any]:
        """simulate() 결과를 기록한다. overrides 는 스윕 점에서 바뀐 소자 값."""
        cid = self.record_circuit(nl)
        rid = result["run_id"]
        self.put_node(rid, "run", rid, {"kind": kind, "analysis": result.get("analysis"), "analysis_kind": result.get("analysis_kind"),
                                        "points": result.get("points"), "elapsed_ms": result.get("elapsed_ms"), "warnings": len(result.get("warnings") or []),
                                        "errors": len(result.get("errors") or []), "question": question, "overrides": overrides or {}, "circuit": cid})
        self.put_edge(rid, "simulates", cid)
        if parent:
            self.put_edge(parent, "has_point", rid)
        for name, value in (result.get("measurements") or {}).items():
            mid = f"{rid}/{name}"
            self.put_node(mid, "measurement", name, {"name": name, "value": value, "run": rid})
            self.put_edge(rid, "measured", mid)
        self.conn.commit()
        return {"run_id": rid, "circuit_id": cid, "variants": [(n["id"], p.get("diffs")) for _, n, p in self.out(cid, "variant_of")]}

    def record_sweep(self, nl: Netlist, result: dict[str, Any], question: str | None = None) -> dict[str, Any]:
        cid = self.record_circuit(nl)
        rid = result["run_id"]
        comp, meas = result.get("component"), result.get("measurement")
        self.put_node(rid, "run", rid, {"kind": "sweep", "analysis": result.get("analysis"), "analysis_kind": result.get("analysis_kind"), "component": comp,
                                        "measurement": meas, "points": result.get("points"), "failed": result.get("failed"), "elapsed_ms": result.get("elapsed_ms"),
                                        "question": question, "circuit": cid})
        self.put_edge(rid, "simulates", cid)
        self.put_edge(rid, "varies", f"{cid}/{comp}", {"values": [r.get("value") for r in result.get("rows") or []]})
        for i, row in enumerate(result.get("rows") or []):
            pid = f"{rid}/p{i:03d}"
            self.put_node(pid, "run", pid, {"kind": "sweep_point", "overrides": {comp: row.get("value")}, "circuit": cid, "analysis_kind": result.get("analysis_kind")})
            self.put_edge(pid, "simulates", cid)
            self.put_edge(rid, "has_point", pid)
            if row.get(meas) is not None:
                self.put_node(f"{pid}/{meas}", "measurement", meas, {"name": meas, "value": row[meas], "run": pid})
                self.put_edge(pid, "measured", f"{pid}/{meas}")
        self.conn.commit()
        return {"run_id": rid, "circuit_id": cid, "points": len(result.get("rows") or [])}

    # ----------------------------------------------------------------- 질의
    def _run_view(self, run: dict[str, Any]) -> dict[str, Any]:
        circuit = self.node(run.get("circuit") or "") or {}
        comps = {n: (run.get("overrides") or {}).get(n, c.get("value")) for n, c in (circuit.get("components") or {}).items()}
        meas = {m["name"]: m.get("value") for _, m, _ in self.out(run["id"], "measured")}
        return {"run_id": run["id"], "kind": run.get("kind"), "at": run.get("created_at"), "circuit": circuit.get("label"), "circuit_id": circuit.get("id"),
                "analysis": run.get("analysis"), "components": comps, "measurements": meas, "question": run.get("question")}

    def query_runs(self, component: str | None = None, value: float | None = None, tolerance_pct: float = 1.0, measurement: str | None = None,
                   value_min: float | None = None, value_max: float | None = None, title_contains: str | None = None, kind: str | None = None,
                   limit: int = 10) -> list[dict[str, Any]]:
        out = []
        rows = self.conn.execute("SELECT id FROM nodes WHERE type='run' ORDER BY created_at DESC, id DESC").fetchall()
        for r in rows:
            run = self.node(r["id"])
            if kind and run.get("kind") != kind:
                continue
            if not kind and run.get("kind") == "sweep":
                continue  # 스윕 자체는 점(sweep_point)으로 펼쳐 보는 편이 유용하다
            v = self._run_view(run)
            if title_contains and title_contains.lower() not in (v["circuit"] or "").lower():
                continue
            if component:
                cval = v["components"].get(component) if component in v["components"] else next((val for n, val in v["components"].items() if n.lower() == component.lower()), None)
                if cval is None:
                    continue
                if value is not None:
                    try:
                        cv = float(cval) if isinstance(cval, (int, float)) else parse_si(str(cval))
                    except NetlistError:
                        continue
                    if abs(cv - value) > abs(value) * tolerance_pct / 100:
                        continue
            if measurement:
                mv = v["measurements"].get(measurement) if measurement in v["measurements"] else next((val for n, val in v["measurements"].items() if n.lower() == measurement.lower()), None)
                if mv is None:
                    continue
                if value_min is not None and mv < value_min:
                    continue
                if value_max is not None and mv > value_max:
                    continue
            out.append(v)
            if len(out) >= limit:
                break
        return out

    def compare_runs(self, a: str, b: str) -> dict[str, Any]:
        ra, rb = self.node(a), self.node(b)
        if not ra or not rb or ra.get("type") != "run" or rb.get("type") != "run":
            raise ValueError(f"실행 없음: {a if not ra else b}")
        va, vb = self._run_view(ra), self._run_view(rb)
        comp_diffs = [{"component": n, "a": va["components"].get(n), "b": vb["components"].get(n)}
                      for n in sorted(set(va["components"]) | set(vb["components"])) if va["components"].get(n) != vb["components"].get(n)]
        meas = {}
        for n in sorted(set(va["measurements"]) | set(vb["measurements"])):
            x, y = va["measurements"].get(n), vb["measurements"].get(n)
            meas[n] = {"a": x, "b": y, "ratio": (y / x if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x else None)}
        return {"a": va, "b": vb, "same_circuit": va["circuit_id"] == vb["circuit_id"], "same_topology": (self.node(va["circuit_id"] or "") or {}).get("topology") == (self.node(vb["circuit_id"] or "") or {}).get("topology"),
                "component_diffs": comp_diffs, "analysis_same": va["analysis"] == vb["analysis"], "measurements": meas}

    def family(self, run_id: str, limit: int = 20) -> dict[str, Any]:
        """이 실행의 회로와 같은 토폴로지인 회로들(값만 다른 변형)과 그 실행·측정을 모아 준다."""
        run = self.node(run_id)
        if not run or run.get("type") != "run":
            raise ValueError(f"실행 없음: {run_id}")
        base = self.node(run["circuit"])
        members = [base] + [n for _, n, _ in self.out(base["id"], "variant_of")]
        out = []
        for c in members:
            diffs = next((p.get("diffs") for _, n, p in self.out(base["id"], "variant_of") if n["id"] == c["id"]), [])
            runs = [self._run_view(self.node(src["id"])) for _, src, _ in self.inbound(c["id"], "simulates") if (self.node(src["id"]) or {}).get("kind") in ("sim", "sweep_point")]
            runs.sort(key=lambda r: (r.get("at") or "", r["run_id"]), reverse=True)
            out.append({"circuit_id": c["id"], "title": c.get("label"), "diff_from_base": diffs, "runs": runs[:limit], "total_runs": len(runs)})
        return {"base_run": run_id, "base_circuit": base["id"], "topology": base.get("topology"), "circuits": out}

    def stats(self) -> dict[str, Any]:
        types = {r[0]: r[1] for r in self.conn.execute("SELECT type, COUNT(*) FROM nodes GROUP BY type")}
        rels = {r[0]: r[1] for r in self.conn.execute("SELECT rel, COUNT(*) FROM edges GROUP BY rel")}
        return {"path": str(self.path), "nodes": types, "edges": rels}

    def export(self) -> dict[str, Any]:
        nodes = [{"id": r["id"], "type": r["type"], "label": r["label"], **json.loads(r["props"])} for r in self.conn.execute("SELECT * FROM nodes")]
        edges = [{"src": r["src"], "rel": r["rel"], "dst": r["dst"], **json.loads(r["props"])} for r in self.conn.execute("SELECT * FROM edges")]
        return {"nodes": nodes, "edges": edges}


# ----------------------------------------------------------------------------- 그림
def render_graph_svg(graph: "ExperimentGraph", max_runs: int = 12) -> str:
    """회로(위) → 실행(가운데) → 측정(아래) 세 층으로 그린다. 같은 토폴로지 변형은 점선으로 잇는다. README·화면용."""
    from xml.sax.saxutils import escape

    INK, SOFT, GRID = "#1B2430", "#5C6672", "#D9DEE3"
    COLOR = {"circuit": "#0E6B63", "run": "#2F5D9E", "measurement": "#B7791F", "sweep": "#6B4C9A"}
    runs = [graph.node(r["id"]) for r in graph.conn.execute("SELECT id FROM nodes WHERE type='run' ORDER BY created_at, id")]
    runs = [r for r in runs if r.get("kind") in ("sim", "sweep")][-max_runs:]
    circuits: list[dict] = []
    for r in runs:
        c = graph.node(r["circuit"])
        if c and c["id"] not in {x["id"] for x in circuits}:
            circuits.append(c)
    meas = [(r["id"], m) for r in runs for _, m, _ in graph.out(r["id"], "measured")][:max_runs * 3]
    W = max(760, 150 * max(len(circuits), len(runs), 1) + 60)
    H = 400
    pos: dict[str, tuple[float, float]] = {}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="Pretendard, -apple-system, sans-serif" font-size="12">']
    for y, label in ((100, "회로 (점선 = 같은 토폴로지, 값만 다른 변형)"), (220, "실행"), (340, "측정값")):
        out.append(f'<text x="16" y="{y - 36}" fill="{SOFT}" font-size="11">{label}</text>')
    def place(items, y, key):
        n = len(items) or 1
        for i, it in enumerate(items):
            pos[key(it)] = (W * (i + 0.5) / n, y)
    place(circuits, 100, lambda c: c["id"])
    place(runs, 220, lambda r: r["id"])
    place(meas, 340, lambda m: m[1]["id"])
    # 간선: 변형 관계는 완전 그래프라 다 그리면 엉킨다 → 값 차이가 가장 작은 쌍만 잇는 최소 신장 트리(크루스칼)
    import math

    def _w(diffs: list[dict]) -> float:
        w = 0.0
        for d in diffs:
            try:
                a, b = parse_si(str(d["from"])), parse_si(str(d["to"]))
                w += abs(math.log10(abs(a) or 1e-30) - math.log10(abs(b) or 1e-30))
            except NetlistError:
                w += 1.5  # 파형 전원처럼 숫자가 아닌 차이
        return w or 0.01

    cand = []
    for c in circuits:
        for _, other, p in graph.out(c["id"], "variant_of"):
            if other["id"] in pos and c["id"] < other["id"]:
                cand.append((_w(p.get("diffs") or []), c["id"], other["id"], p.get("diffs") or []))
    parent = {c["id"]: c["id"] for c in circuits}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    k = 0
    for w, a, b, d in sorted(cand, key=lambda t: t[0]):
        if find(a) == find(b):
            continue
        parent[find(a)] = find(b)
        (x1, y1), (x2, y2) = pos[a], pos[b]
        lab = ", ".join(f"{x['component']} {str(x['from'])[:8]}→{str(x['to'])[:8]}" for x in d[:2])
        lift = 50 + 22 * (k % 2)
        out.append(f'<path d="M{x1:.0f} {y1 - 18} Q{(x1 + x2) / 2:.0f} {y1 - lift - 10} {x2:.0f} {y2 - 18}" fill="none" stroke="{COLOR["circuit"]}" stroke-dasharray="5 4" stroke-width="1.2"/>')
        out.append(f'<text x="{(x1 + x2) / 2:.0f}" y="{y1 - lift + 8}" text-anchor="middle" fill="{COLOR["circuit"]}" font-size="10">{escape(lab)}</text>')
        k += 1
    for r in runs:
        if r["circuit"] in pos:
            (x1, y1), (x2, y2) = pos[r["id"]], pos[r["circuit"]]
            out.append(f'<line x1="{x1:.0f}" y1="{y1 - 16}" x2="{x2:.0f}" y2="{y2 + 16}" stroke="{GRID}" stroke-width="1.2"/>')
    for rid, m in meas:
        if rid in pos:
            (x1, y1), (x2, y2) = pos[m["id"]], pos[rid]
            out.append(f'<line x1="{x1:.0f}" y1="{y1 - 14}" x2="{x2:.0f}" y2="{y2 + 16}" stroke="{GRID}" stroke-width="1.2"/>')
    # 노드
    for c in circuits:
        x, y = pos[c["id"]]
        comps = c.get("components") or {}
        vals = " ".join(f"{n}={v.get('value')}" for n, v in list(comps.items())[:3] if v.get("value"))
        out.append(f'<rect x="{x - 66:.0f}" y="{y - 18}" width="132" height="36" rx="8" fill="#E6F2F0" stroke="{COLOR["circuit"]}"/>')
        out.append(f'<text x="{x:.0f}" y="{y - 3}" text-anchor="middle" fill="{INK}" font-weight="600">{escape((c.get("label") or "").split("(")[0].strip()[:16])}</text>')
        out.append(f'<text x="{x:.0f}" y="{y + 11}" text-anchor="middle" fill="{SOFT}" font-size="10">{escape(vals[:30])}</text>')
    for r in runs:
        x, y = pos[r["id"]]
        col = COLOR["sweep"] if r.get("kind") == "sweep" else COLOR["run"]
        sub = f"{r.get('analysis_kind', '')} · {r.get('points') or 0}점" if r.get("kind") == "sim" else f"스윕 {r.get('component')} {r.get('points')}점"
        out.append(f'<rect x="{x - 60:.0f}" y="{y - 16}" width="120" height="32" rx="6" fill="#fff" stroke="{col}"/>')
        out.append(f'<text x="{x:.0f}" y="{y - 3}" text-anchor="middle" fill="{INK}" font-size="11">{escape(r["id"])}</text>')
        out.append(f'<text x="{x:.0f}" y="{y + 10}" text-anchor="middle" fill="{SOFT}" font-size="10">{escape(sub)}</text>')
    for rid, m in meas:
        x, y = pos[m["id"]]
        v = m.get("value")
        txt = f"{m['name']} = {v:.4g}" if isinstance(v, (int, float)) else f"{m['name']} = -"
        out.append(f'<rect x="{x - 48:.0f}" y="{y - 14}" width="96" height="28" rx="14" fill="#FDF4DC" stroke="{COLOR["measurement"]}"/>')
        out.append(f'<text x="{x:.0f}" y="{y + 4}" text-anchor="middle" fill="{INK}" font-size="11">{escape(txt[:16])}</text>')
    out.append("</svg>")
    return "\n".join(out)
