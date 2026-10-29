"""문서 그래프: 규정·절차 문서 사이의 관계를 그래프로 두고 "이 문서를 바꾸면 같이 봐야 하는 문서" 를 답한다.

노드 = 문서(문서번호·제목·담당·개정일·절 목록). 간선 세 종류:
- 참조: 본문이 다른 문서의 번호(CS-002)·제목·짧은 제목("보상 규정", "반품 규정")을 직접 적음. 방향 있음
- 주제: 본문이 다른 문서의 절 제목(예: "기본 할인")을 그대로 쓰는 경우. 방향 있음, 라벨은 절 이름
- 데이터: 같은 데이터 개념(안전재고 = inventory.safety_stock, 지연 사유 = orders.delay_reason …)을 다룸. 방향 없음
개념 사전은 팩이 준다(문서가 DB 열 이름을 거의 안 쓰고 한국어 업무 용어를 쓰기 때문). 머리 `문서번호: XXX-000` 와
`## 절` 구조의 마크다운이면 어느 팩이든 그대로 쓴다. 외부 라이브러리 없음.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

# 제목의 짧은 형태를 만들 때 단독으로 쓰면 뜻이 없는 단어(이 단어로 시작하는 2어절 별칭은 버린다)
GENERIC = {
    "규정",
    "절차",
    "기준",
    "지침",
    "정의",
    "개요",
    "목적",
    "적용",
    "범위",
    "기타",
    "예외",
    "참고",
    "비고",
    "원칙",
    "처리",
    "관리",
    "운영",
    "기록",
    "및",
    "일정",
}

# 개념 → (데이터 위치, 본문에서 그 개념을 뜻하는 표현들)
Concept = tuple[str, tuple[str, ...]]
DEFAULT_CONCEPTS: dict[str, Concept] = {}


@dataclass
class DocNode:
    id: str  # 문서번호(없으면 파일 stem)
    stem: str
    title: str
    team: str = ""
    revised: str = ""
    sections: list[str] = field(default_factory=list)
    body: str = ""
    concepts: list[str] = field(default_factory=list)


@dataclass
class Edge:
    src: str
    dst: str
    kind: str  # 참조 | 주제 | 데이터
    label: str


def title_aliases(title: str) -> list[str]:
    """'배송 지연 보상 규정' → ['배송 지연 보상 규정', '배송 지연 보상', '보상 규정']. '반품 및 교환 규정' → '반품 규정', '교환 규정' 도."""
    words = title.split()
    out = [title]
    if "및" in words:
        i = words.index("및")
        tail = words[i + 2 :]
        for w in (words[i - 1], words[i + 1]):
            out.append(" ".join([w] + tail))
    if len(words) >= 3:
        out.append(" ".join(words[:-1]))
        if words[-2] not in GENERIC:
            out.append(" ".join(words[-2:]))
    return list(dict.fromkeys(a for a in out if len(a) >= 4))


class DocGraph:
    def __init__(self, nodes: list[DocNode], edges: list[Edge], concepts: dict[str, Concept]):
        self.nodes = {n.id: n for n in nodes}
        self.edges = edges
        self.concepts = concepts

    # ---------------------------------------------------------------- 만들기
    @classmethod
    def build(cls, docs_dir: Path, concepts: dict[str, Concept] | None = None) -> DocGraph:
        concepts = concepts if concepts is not None else DEFAULT_CONCEPTS
        nodes: list[DocNode] = []
        for p in sorted(Path(docs_dir).glob("*.md")):
            text = p.read_text(encoding="utf-8")
            lines = text.splitlines()
            title = lines[0].lstrip("# ").strip() if lines else p.stem
            head = "\n".join(lines[:4])
            num = re.search(r"문서번호:\s*([A-Z]+-\d+)", head)
            team = re.search(r"담당:\s*([^|\n]+)", head)
            rev = re.search(r"개정일:\s*([\d-]+)", head)
            sections = [ln.lstrip("# ").strip() for ln in lines if ln.startswith("## ")]
            body = "\n".join(ln for ln in lines[1:] if not ln.startswith("문서번호:"))
            found = [c for c, (_, kws) in concepts.items() if any(k in body for k in kws)]
            nodes.append(
                DocNode(
                    num.group(1) if num else p.stem,
                    p.stem,
                    title,
                    team.group(1).strip() if team else "",
                    rev.group(1) if rev else "",
                    sections,
                    body,
                    found,
                )
            )
        edges: list[Edge] = []
        seen: set[tuple[str, str, str]] = set()

        def add(src: str, dst: str, kind: str, label: str) -> None:
            key = (src, dst, kind)
            if src != dst and key not in seen:
                seen.add(key)
                edges.append(Edge(src, dst, kind, label))

        for a in nodes:
            for b in nodes:
                if a is b:
                    continue
                hit = next((al for al in [b.id, *title_aliases(b.title)] if al in a.body), None)
                if hit:
                    add(a.id, b.id, "참조", hit)
                    continue
                for sec in b.sections:
                    if len(sec) >= 4 and sec not in GENERIC and sec in a.body:
                        add(a.id, b.id, "주제", sec)
                        break
        ids = [n.id for n in nodes]
        by_id = {n.id: n for n in nodes}
        for i, x in enumerate(ids):
            for y in ids[i + 1 :]:
                shared = [c for c in by_id[x].concepts if c in by_id[y].concepts]
                if shared:
                    label = ", ".join(shared)
                    add(x, y, "데이터", label)
                    add(y, x, "데이터", label)
        return cls(nodes, edges, concepts)

    # ---------------------------------------------------------------- 질의
    def resolve(self, name: str) -> DocNode | None:
        """문서번호·파일 이름·제목(일부)로 노드를 찾는다. 여럿이면 None."""
        key = name.strip().lower()
        for n in self.nodes.values():
            if key in (n.id.lower(), n.stem.lower(), n.title.lower()):
                return n
        hits = [n for n in self.nodes.values() if key in n.title.lower() or key in n.stem.lower()]
        if len(hits) == 1:
            return hits[0]
        hits = [n for n in self.nodes.values() if any(key == al.lower() for al in title_aliases(n.title))]
        return hits[0] if len(hits) == 1 else None

    def _not_found(self, name: str) -> ValueError:
        return ValueError(
            f"문서를 찾지 못했습니다: {name!r}. 문서번호나 제목으로 주세요. 있는 문서: "
            + ", ".join(f"{n.id} {n.title}" for n in self.nodes.values())
        )

    def _row(self, doc_id: str, kind: str, label: str) -> dict[str, Any]:
        n = self.nodes[doc_id]
        return {"doc": n.id, "title": n.title, "team": n.team, "kind": kind, "label": label}

    def related(self, name: str) -> dict[str, Any]:
        n = self.resolve(name)
        if n is None:
            raise self._not_found(name)
        refs = [self._row(e.dst, e.kind, e.label) for e in self.edges if e.src == n.id and e.kind != "데이터"]
        by = [self._row(e.src, e.kind, e.label) for e in self.edges if e.dst == n.id and e.kind != "데이터"]
        data = [self._row(e.dst, e.kind, e.label) for e in self.edges if e.src == n.id and e.kind == "데이터"]
        return {
            "doc": n.id,
            "title": n.title,
            "team": n.team,
            "revised": n.revised,
            "sections": n.sections,
            "concepts": [{"name": c, "data": self.concepts[c][0]} for c in n.concepts],
            "references": refs,
            "referenced_by": by,
            "shares_data_with": data,
        }

    def impact(self, name: str, depth: int = 2) -> dict[str, Any]:
        """이 문서를 바꾸면 같이 봐야 하는 문서. 이 문서를 참조·주제로 끌어다 쓰는 쪽을 depth 단계까지 따라가고,
        같은 데이터 개념을 다루는 문서는 따로(1단계) 묶는다."""
        n = self.resolve(name)
        if n is None:
            raise self._not_found(name)
        found: dict[str, dict[str, Any]] = {}
        frontier = [(n.id, 0)]
        while frontier:
            cur, d = frontier.pop(0)
            if d >= depth:
                continue
            for e in self.edges:
                if e.dst != cur or e.kind == "데이터" or e.src == n.id or e.src in found:
                    continue
                m = self.nodes[e.src]
                via = (
                    f"{e.kind}: {e.label}" if cur == n.id else f"{self.nodes[cur].title} 를 거쳐 ({e.kind}: {e.label})"
                )
                found[e.src] = {"doc": m.id, "title": m.title, "team": m.team, "via": via, "depth": d + 1}
                frontier.append((e.src, d + 1))
        chain = sorted(found.values(), key=lambda r: (r["depth"], r["doc"]))
        data = [
            {
                "doc": e.dst,
                "title": self.nodes[e.dst].title,
                "team": self.nodes[e.dst].team,
                "concepts": e.label,
                "data": ", ".join(self.concepts[c][0] for c in e.label.split(", ") if c in self.concepts),
            }
            for e in self.edges
            if e.src == n.id and e.kind == "데이터" and e.dst not in found
        ]
        return {
            "doc": n.id,
            "title": n.title,
            "impacted": chain,
            "shares_data": data,
            "count": len(chain) + len(data),
            "note": "impacted 는 이 문서를 인용하는 문서(참조·주제, 최대 2단계). shares_data 는 같은 데이터 개념을 다루는 문서라 수치·기준이 바뀌면 함께 볼 것.",
        }

    def stats(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for e in self.edges:
            kinds[e.kind] = kinds.get(e.kind, 0) + (0.5 if e.kind == "데이터" else 1)
        return {
            "docs": len(self.nodes),
            "edges": sum(int(v) for v in kinds.values()),
            "by_kind": {k: int(v) for k, v in kinds.items()},
        }

    def table(self) -> list[dict[str, Any]]:
        return [
            {
                "문서": n.id,
                "제목": n.title,
                "담당": n.team,
                "개정일": n.revised,
                "인용함": sum(1 for e in self.edges if e.src == n.id and e.kind != "데이터"),
                "인용됨": sum(1 for e in self.edges if e.dst == n.id and e.kind != "데이터"),
                "데이터 개념": ", ".join(n.concepts),
            }
            for n in self.nodes.values()
        ]

    # ---------------------------------------------------------------- 그림
    def render_svg(self, focus: str | None = None) -> str:
        """문서를 원 위에 팀별로 모아 놓고 간선을 현(弦)으로 긋는다(현은 다른 상자를 지나지 않는다).
        참조·주제는 화살표, 같은 데이터는 가는 점선. README·데이터 탭용."""
        from math import cos, pi, sin

        order = sorted(self.nodes.values(), key=lambda n: (n.team, n.id))
        count = max(len(order), 1)
        hw, hh = 84, 20  # 상자 반폭·반높이
        radius = max(170, 34 * count)
        cx, cy = radius + hw + 24, radius + hh + 28
        width, height = int(cx * 2), int(cy * 2 + 26)
        pos: dict[str, tuple[float, float]] = {}
        for i, n in enumerate(order):
            ang = -pi / 2 + 2 * pi * i / count
            pos[n.id] = (cx + radius * cos(ang), cy + radius * sin(ang))

        def clip(src: tuple[float, float], dst: tuple[float, float], gap: float = 3) -> tuple[float, float]:
            """src → dst 선이 dst 상자 테두리와 만나는 점(상자 안으로 들어가지 않게)."""
            dx, dy = dst[0] - src[0], dst[1] - src[1]
            dist = max((dx * dx + dy * dy) ** 0.5, 1e-9)
            ux, uy = dx / dist, dy / dist
            k = min(hw / abs(ux) if ux else 1e9, hh / abs(uy) if uy else 1e9) + gap
            return dst[0] - ux * k, dst[1] - uy * k

        color = {"참조": "#1f4e79", "주제": "#1a9c8f", "데이터": "#c3bfb6"}
        out = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            'font-family="Pretendard, -apple-system, Apple SD Gothic Neo, sans-serif" font-size="12">',
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
            '<path d="M0,0 L10,5 L0,10 z" fill="#3b4656"/></marker></defs>',
            f'<rect width="{width}" height="{height}" fill="#f4f3f0"/>',
        ]
        drawn: set[tuple[str, str]] = set()
        for e in sorted(self.edges, key=lambda e: e.kind != "데이터"):  # 점선을 먼저 깔고 화살표를 위에
            a, b = pos[e.src], pos[e.dst]
            if e.kind == "데이터":
                pair = tuple(sorted((e.src, e.dst)))
                if pair in drawn:
                    continue
                drawn.add(pair)
                p1, p2 = clip(b, a, 0), clip(a, b, 0)
                out.append(
                    f'<line x1="{p1[0]:.0f}" y1="{p1[1]:.0f}" x2="{p2[0]:.0f}" y2="{p2[1]:.0f}" stroke="{color["데이터"]}" stroke-width="1" stroke-dasharray="3 4"/>'
                )
                continue
            p1, p2 = clip(b, a, 1), clip(a, b)
            out.append(
                f'<line x1="{p1[0]:.0f}" y1="{p1[1]:.0f}" x2="{p2[0]:.0f}" y2="{p2[1]:.0f}" stroke="{color[e.kind]}" stroke-width="1.8" marker-end="url(#arrow)"/>'
            )
            mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
            out.append(
                f'<text x="{mx:.0f}" y="{my - 4:.0f}" text-anchor="middle" fill="{color[e.kind]}" font-size="10.5" paint-order="stroke" stroke="#f4f3f0" stroke-width="3">{escape(e.label)}</text>'
            )
        for n in order:
            x, y = pos[n.id]
            hi = focus is not None and n.id == focus
            out.append(
                f'<rect x="{x - hw:.0f}" y="{y - hh:.0f}" width="{hw * 2}" height="{hh * 2}" rx="6" fill="#ffffff" stroke="{"#1f4e79" if hi else "#bfbcb5"}" stroke-width="{2 if hi else 1}"/>'
            )
            out.append(
                f'<text x="{x:.0f}" y="{y - 3:.0f}" text-anchor="middle" fill="#1d2430" font-weight="600">{escape(n.title)}</text>'
            )
            out.append(
                f'<text x="{x:.0f}" y="{y + 12:.0f}" text-anchor="middle" fill="#6f7580" font-size="10.5">{escape(n.id)} · {escape(n.team)}</text>'
            )
        out.append(
            f'<text x="16" y="{height - 10}" fill="#5a6270" font-size="11"><tspan fill="{color["참조"]}" font-weight="700">→</tspan> 참조(번호·제목 인용)   '
            f'<tspan fill="{color["주제"]}" font-weight="700">→</tspan> 주제(절 제목 인용)   <tspan fill="#9a958b">┄</tspan> 같은 데이터 개념</text>'
        )
        out.append("</svg>")
        return "\n".join(out)
