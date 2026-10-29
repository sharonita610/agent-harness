"""소자 라이브러리: 자주 쓰는 다이오드·트랜지스터·MOSFET·연산증폭기의 SPICE 모델 카드.

교과서식 근사 모델(제조사 모델 아님). 도구 search_parts / get_part 로 찾고, run_simulation·run_sweep 의 parts 인자에
이름을 주면 모델 카드가 넷리스트 끝에 자동으로 붙는다. 넷리스트 가드가 허용하는 .model/.subckt 만 들어 있다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

LIBRARY_FILE = Path(__file__).resolve().parent / "library" / "parts.json"
KINDS = ("diode", "npn", "pnp", "nmos", "pmos", "opamp")
ELEMENT_LETTER = {
    "diode": "D",
    "npn": "Q",
    "pnp": "Q",
    "nmos": "M",
    "pmos": "M",
    "opamp": "X",
}  # 종류별 SPICE 소자 첫 글자
KIND_KO = {
    "diode": "다이오드",
    "npn": "NPN 트랜지스터",
    "pnp": "PNP 트랜지스터",
    "nmos": "N채널 MOSFET",
    "pmos": "P채널 MOSFET",
    "opamp": "연산증폭기",
}


class PartLibrary:
    def __init__(self, path: Path = LIBRARY_FILE):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.note = (
            data.get("_note", "")
            + " 전류원은 I1 a b 가 a → b 로 흐르므로 순방향 바이어스는 I1 0 anode 처럼 접지를 첫 노드로 둔다."
        )
        self.parts: list[dict[str, Any]] = data["parts"]
        self._by_name = {p["name"].lower(): p for p in self.parts}
        self._by_model = {p["model"].lower(): p for p in self.parts}

    def get(self, name: str) -> dict[str, Any] | None:
        key = name.strip().lower()
        return self._by_name.get(key) or self._by_model.get(key)

    def search(self, query: str = "", kind: str | None = None, k: int = 10) -> list[dict[str, Any]]:
        """이름·설명·태그에 질의 단어가 들어가면 점수. kind 로 종류를 좁힌다."""
        words = [w for w in re.split(r"[\s,/]+", (query or "").lower()) if w]
        out = []
        for p in self.parts:
            if kind and p["kind"] != kind:
                continue
            hay = " ".join(
                [p["name"], p["model"], p["title"], p["kind"], KIND_KO.get(p["kind"], ""), *p.get("tags", [])]
            ).lower()
            score = sum(2 if w == p["name"].lower() else 1 for w in words if w in hay)
            if not words or score:
                out.append((score, p))
        out.sort(key=lambda x: (-x[0], x[1]["name"]))
        return [self.summary(p) for _, p in out[:k]]

    @staticmethod
    def summary(p: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": p["name"],
            "model": p["model"],
            "kind": KIND_KO.get(p["kind"], p["kind"]),
            "title": p["title"],
            "params": p["params"],
        }

    def cards(self, names: list[str]) -> str:
        """parts 이름 목록 → 넷리스트 끝에 붙일 모델 카드 텍스트. 모르는 이름은 ValueError."""
        lines = []
        for n in names:
            p = self.get(n)
            if p is None:
                raise ValueError(
                    f"라이브러리에 없는 소자: {n!r}. search_parts 로 이름을 확인하세요. 있는 것: {[x['name'] for x in self.parts]}"
                )
            lines.append(p["card"])
        return "\n".join(lines)

    def with_parts(self, netlist: str, names: list[str] | None) -> str:
        """넷리스트에 모델 카드를 붙인다. 평가에서 모델이 `.model 1N4148 D`(기본값 다이오드)를 직접 적고 parts 도 주는 경우가 있어,
        parts 로 명시한 이름과 같은 한 줄 .model 은 라이브러리 카드로 바꾼다(요청한 쪽이 이긴다). .subckt 는 여러 줄이라 건너뛴다."""
        return self.apply_parts(netlist, names)[0]

    def apply_parts(self, netlist: str, names: list[str] | None) -> tuple[str, list[str]]:
        """with_parts 와 같되 무엇을 했는지 메모도 돌려준다. 평가에서 본 소형 모델의 습관 두 가지도 흡수한다:
        ① 소자 줄에 model 이름(D1N4148) 대신 부품 이름(1N4148)을 씀 → model 이름으로 바꿈.
        ② 제너를 `Z1 …`(SPICE 에선 MESFET), 연산증폭기를 `U1 …` 처럼 종류에 안 맞는 첫 글자로 씀 → 맞는 글자를 앞에 붙임(Z1 → DZ1)."""
        names = list(dict.fromkeys(n.strip() for n in (names or []) if n and n.strip()))  # 같은 이름은 한 번만
        if not names:
            return netlist, []
        for n in names:
            if self.get(n) is None:
                self.cards([n])  # ValueError 로 안내
        text = netlist.rstrip()
        body, end = text, ""
        m = re.search(r"\n\s*\.end\s*$", text, re.I)
        if m:
            body, end = text[: m.start()], "\n.end"
        parts = {n: self.get(n) for n in names}
        wanted = {p["model"].lower(): n for n, p in parts.items()}
        kept: list[str] = []
        notes: list[str] = []
        have_subckt: set[str] = set()
        for ln in body.splitlines():
            toks = ln.split()
            head = toks[0].lower() if toks else ""
            if head == ".model" and len(toks) > 1 and toks[1].lower() in wanted:
                notes.append(f"{wanted[toks[1].lower()]}: 넷리스트의 '.model {toks[1]}' 을 라이브러리 카드로 대체")
                continue
            if head == ".subckt" and len(toks) > 1:
                have_subckt.add(toks[1].lower())
            if head and head[0].isalpha():  # 소자 줄
                ln = self._fix_element_line(ln, parts, notes)
            kept.append(ln)
        add = []
        for n in names:
            part = parts[n]
            if part["model"].lower() in have_subckt:
                notes.append(f"{n}: 넷리스트에 같은 이름의 .subckt 가 있어 라이브러리 카드를 넣지 않음")
                continue
            add.append(part["card"])
            if not any(x.startswith(f"{n}:") for x in notes):
                notes.append(f"{n}: 라이브러리 카드 추가")
        return "\n".join(kept) + ("\n" + "\n".join(add) if add else "") + end + "\n", notes

    @staticmethod
    def _fix_element_line(line: str, parts: dict[str, dict[str, Any]], notes: list[str]) -> str:
        toks = line.split()
        for n, p in parts.items():
            if p["name"].lower() != p["model"].lower():
                pat = re.compile(r"(?<![\w])" + re.escape(p["name"]) + r"(?![\w])", re.I)
                if any(pat.fullmatch(t) for t in toks[1:]):
                    line = pat.sub(p["model"], line)
                    toks = line.split()
                    notes.append(
                        f"{n}: 소자 줄 '{toks[0]}' 의 부품 이름 {p['name']} 을 model 이름 {p['model']} 로 바꿈"
                    )
            if any(t.lower() == p["model"].lower() for t in toks[1:]):
                letter = ELEMENT_LETTER[p["kind"]]
                if toks[0][0].upper() != letter:
                    new_name = letter + toks[0]
                    notes.append(
                        f"{n}: 소자 줄 '{toks[0]}' 의 첫 글자가 {KIND_KO.get(p['kind'], p['kind'])} 의 SPICE 글자 {letter} 가 아니라 '{new_name}' 로 바꿈"
                    )
                    line = new_name + line[len(toks[0]) :]
                    toks = line.split()
        return line


LIBRARY = PartLibrary()
