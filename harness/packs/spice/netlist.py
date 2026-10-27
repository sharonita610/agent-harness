"""SPICE 넷리스트 가드·파서. 모델이 쓴 회로 텍스트를 ngspice 에 넘기기 전에 검사하고 요약한다.

SQL 가드(enterprise.tools.guard_select)와 같은 역할: 파일 접근·셸 실행 같은 위험한 지시문을 막고,
소형 모델이 자주 틀리는 부분(첫 줄이 제목으로 먹히는 것, .end 누락, M 과 meg 혼동)을 바로잡거나 경고한다.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

MAX_CHARS = 20_000
MAX_LINES = 400

# ngspice 는 첫 줄을 무조건 제목으로 읽는다. 첫 줄이 소자나 지시문이면 회로에서 사라진다.
_ELEMENT_LETTERS = set("RLCKVIDQMJZEFGHBSWTUOXYAP")
_ANALYSES = (".op", ".dc", ".ac", ".tran", ".noise", ".tf", ".pz", ".disto", ".sens")
# 제어 블록은 시뮬레이터 모듈이 직접 붙이므로, 모델 입력에서는 회로 기술 지시문만 허용한다(허용 목록 방식).
# .include/.lib(파일 읽기), .control(셸·파일 명령 실행 가능), .meas/.print/.save(제어 블록 역할) 는 모두 여기서 걸린다.
_ALLOWED_DOT = (".model", ".subckt", ".ends", ".param", ".params", ".option", ".options", ".temp", ".ic", ".nodeset", ".global",
                ".func", ".if", ".elseif", ".else", ".endif", ".title", ".end")
_VALUE_KEYWORDS = ("dc", "ac", "pulse", "sin", "pwl", "exp", "sffm", "am", "trnoise", "trrandom")
# 접미사: SPICE 는 대소문자를 구분하지 않고 M = 밀리, MEG = 메가 다.
_SUFFIX = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15, "mil": 25.4e-6}
_NUM = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Zµ]*)\s*$")


class NetlistError(ValueError):
    pass


def parse_si(token: str) -> float:
    """'1k' → 1000, '100n' → 1e-7, '1meg' → 1e6, '10mH' → 0.01, '4.7uF' → 4.7e-6, '12' → 12."""
    m = _NUM.match(str(token))
    if not m:
        raise NetlistError(f"숫자로 읽을 수 없음: {token!r}")
    value, unit = float(m.group(1)), m.group(2).lower()
    if unit.startswith("meg"):
        return value * 1e6
    if unit.startswith("mil"):
        return value * 25.4e-6
    if unit and unit[0] in _SUFFIX:
        return value * _SUFFIX[unit[0]]
    return value


def format_si(value: float, unit: str = "", digits: int = 4) -> str:
    """1591.5 → '1.592 kHz'. 답변·그래프 라벨용(SPICE 입력용이 아니다: 여기선 M 이 메가)."""
    if value == 0 or not math.isfinite(value):
        return f"{value:g} {unit}".strip()
    exp = int(math.floor(math.log10(abs(value)) / 3) * 3)
    exp = max(-15, min(12, exp))
    prefix = {12: "T", 9: "G", 6: "M", 3: "k", 0: "", -3: "m", -6: "µ", -9: "n", -12: "p", -15: "f"}[exp]
    scaled = value / (10 ** exp)
    return f"{scaled:.{digits}g} {prefix}{unit}".strip()


@dataclass
class Component:
    name: str
    kind: str       # 첫 글자(대문자): R L C V I D Q M X ...
    nodes: list[str]
    value: str | None   # R/L/C/DC 소스의 값 토큰(원문)
    line_no: int


@dataclass
class Netlist:
    title: str
    lines: list[str]                 # 제목 제외, .end 제외, 정리된 본문
    components: list[Component] = field(default_factory=list)
    analyses: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    subckts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    inline_measurements: list[str] = field(default_factory=list)   # 넷리스트에 섞여 있던 .measure 줄(앞의 .measure 제거)

    @property
    def nodes(self) -> list[str]:
        seen: dict[str, None] = {}
        for c in self.components:
            for n in c.nodes:
                seen.setdefault(n, None)
        return list(seen)

    @property
    def analysis_kind(self) -> str | None:
        """첫 해석의 종류: op | dc | ac | tran | ..."""
        return self.analyses[0].split()[0][1:].lower() if self.analyses else None

    def text(self) -> str:
        """ngspice 에 넘길 본문(제목 포함, .end 없음). 제어 블록은 시뮬레이터가 붙인다."""
        return "\n".join([self.title, *self.lines])

    def summary(self) -> dict:
        return {
            "title": self.title.lstrip("* ").strip(),
            "components": [{"name": c.name, "type": c.kind, "nodes": c.nodes, "value": c.value} for c in self.components],
            "nodes": self.nodes,
            "analyses": self.analyses,
            "models": self.models,
            "subcircuits": self.subckts,
            "warnings": self.warnings,
        }


_NODE_COUNT = {"R": 2, "L": 2, "C": 2, "V": 2, "I": 2, "D": 2, "B": 2, "S": 4, "W": 2, "Q": 3, "J": 3, "Z": 3, "M": 4, "E": 4, "G": 4, "F": 2, "H": 2, "T": 4, "O": 4, "U": 3, "Y": 2, "P": 2}


def _split_lines(text: str) -> list[str]:
    """줄 연속(+), 주석($ ;), CRLF 정리."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    for ln in raw:
        ln = re.split(r"\s[;$]", " " + ln, maxsplit=1)[0].strip() if not ln.lstrip().startswith("*") else ln.strip()
        if not ln:
            continue
        if ln.startswith("+") and out:
            out[-1] = out[-1] + " " + ln[1:].strip()
        else:
            out.append(ln)
    return out


def _looks_like_component(parts: list[str]) -> bool:
    """첫 줄이 제목인지 소자인지 가른다. 'RC low-pass filter' 는 제목, 'R1 in out 1k' 와 'Rload out 0 1k' 는 소자."""
    if len(parts) < 3 or parts[0][0].upper() not in _ELEMENT_LETTERS:
        return False
    kind = parts[0][0].upper()
    if kind in "RLC":
        if len(parts) < 4:
            return False
        try:
            parse_si(parts[3])
            return True
        except NetlistError:
            return parts[3].startswith("{")
    if kind in "VI":
        return len(parts) >= 4 and (parts[3].lower().startswith(_VALUE_KEYWORDS) or re.match(r"^[-+.\d]", parts[3]) is not None)
    return any(ch.isdigit() for ch in parts[0]) or "0" in parts[1:]


def parse_netlist(text: str) -> Netlist:
    """가드 + 파싱. 위험한 지시문은 NetlistError, 흔한 실수는 고치거나 warnings 에 적는다."""
    if not text or not text.strip():
        raise NetlistError("넷리스트가 비어 있습니다.")
    if len(text) > MAX_CHARS:
        raise NetlistError(f"넷리스트가 너무 큽니다({len(text)}자 > {MAX_CHARS}자).")
    lines = _split_lines(text)
    if len(lines) > MAX_LINES:
        raise NetlistError(f"줄이 너무 많습니다({len(lines)} > {MAX_LINES}).")
    for ln in lines:
        low = ln.lower()
        if low.startswith((".meas", ".measure")):
            continue  # 측정식으로 흡수한다(아래)
        if low.startswith(".") and not low.startswith(_ANALYSES) and not low.startswith(_ALLOWED_DOT):
            raise NetlistError(f"허용되지 않는 지시문: {ln.split()[0]}. 파일(.include/.lib)·제어 블록(.control)·출력(.meas/.print/.save) 지시문은 쓸 수 없습니다. "
                               "측정은 run_simulation 의 measurements 로 지정하세요.")

    warnings: list[str] = []
    # 제목 줄: 첫 줄이 소자/지시문이면 제목을 새로 만들어 끼운다(ngspice 가 첫 줄을 제목으로 삼키는 함정).
    first = lines[0]
    if first.startswith("*") or first.lower().startswith(".title"):
        title = first if first.startswith("*") else "* " + first.split(None, 1)[1] if len(first.split(None, 1)) > 1 else "* circuit"
        body = lines[1:]
    elif first.startswith(".") or _looks_like_component(first.split()):
        title = "* circuit"
        body = lines
        warnings.append("첫 줄이 소자여서 제목 줄('* circuit')을 앞에 넣었습니다. ngspice 는 첫 줄을 항상 제목으로 읽습니다.")
    else:
        title = "* " + first
        body = lines[1:]
    # .end 제거(여러 개여도), .ends 는 유지
    body = [ln for ln in body if ln.lower().rstrip() != ".end"]

    inline_meas = [ln for ln in body if ln.lower().startswith((".meas", ".measure"))]
    body = [ln for ln in body if not ln.lower().startswith((".meas", ".measure"))]
    nl = Netlist(title=title, lines=body, warnings=warnings, inline_measurements=[re.sub(r"^\.\w+\s+", "", m) for m in inline_meas])
    if inline_meas:
        warnings.append(f"넷리스트 안의 .measure {len(inline_meas)}개를 measurements 로 옮겼습니다(다음부터는 measurements 인자로 주세요).")
    in_subckt = False
    for i, ln in enumerate(body, start=2):
        low = ln.lower()
        if low.startswith("*"):
            continue
        if low.startswith(".subckt"):
            in_subckt = True
            parts = ln.split()
            nl.subckts.append(parts[1] if len(parts) > 1 else "?")
            continue
        if low.startswith(".ends"):
            in_subckt = False
            continue
        if low.startswith(".model"):
            parts = ln.split()
            nl.models.append(parts[1] if len(parts) > 1 else "?")
            continue
        if low.startswith(_ANALYSES):
            nl.analyses.append(ln)
            continue
        if low.startswith("."):
            continue  # .param .option .temp .ic .nodeset .global 등은 통과
        if in_subckt:
            continue  # 서브서킷 내부 소자는 최상위 노드 집계에서 제외
        parts = ln.split()
        kind = parts[0][0].upper()
        if kind not in _ELEMENT_LETTERS:
            warnings.append(f"{i}행: 알 수 없는 소자 '{parts[0]}' (ngspice 가 거부할 수 있음)")
            continue
        if kind == "X":
            nodes = parts[1:-1]
            value = parts[-1] if len(parts) > 1 else None
        elif kind == "K":
            nodes, value = [], parts[-1] if len(parts) > 1 else None
        else:
            n = _NODE_COUNT.get(kind, 2)
            nodes = parts[1:1 + n]
            rest = parts[1 + n:]
            value = None
            if kind in "RLC" and rest:
                value = rest[0]
            elif kind in "VI" and rest:
                # V1 a b dc 12 | V1 a b 12 | V1 a b dc 0 ac 1 | V1 a b PULSE(...)
                low_rest = [r.lower() for r in rest]
                if "dc" in low_rest and low_rest.index("dc") + 1 < len(rest):
                    value = rest[low_rest.index("dc") + 1]
                elif rest and re.match(r"^[-+.\d]", rest[0]):
                    value = rest[0]
                else:
                    value = " ".join(rest)
            if kind in "RLC" and value is not None and not value.startswith("{"):
                try:
                    parse_si(value)
                except NetlistError:
                    warnings.append(f"{i}행: {parts[0]} 의 값 '{value}' 을 숫자로 읽을 수 없습니다.")
                else:
                    if re.fullmatch(r"[-+.\d]+[eE]?[-+]?\d*M[a-zA-Z]*", value) and kind in "RL":
                        warnings.append(f"{i}행: {parts[0]}={value} 에서 'M' 은 SPICE 에서 밀리(1e-3)입니다. 메가는 'meg' 로 씁니다.")
        nl.components.append(Component(parts[0], kind, nodes, value, i))

    if not nl.components:
        raise NetlistError("소자가 하나도 없습니다. 예: 'R1 in out 1k'")
    if "0" not in nl.nodes and not any(n.lower() == "gnd" for n in nl.nodes):
        nl.warnings.append("접지 노드 '0' 이 없습니다. ngspice 는 기준 노드 0 을 요구합니다.")
    # 노드에 소자가 하나만 붙어 있으면 떠 있는(floating) 노드 → 특이 행렬 오류의 원인
    deg: dict[str, int] = {}
    for c in nl.components:
        for n in c.nodes:
            deg[n] = deg.get(n, 0) + 1
    dangling = [n for n, d in deg.items() if d == 1 and n != "0"]
    if dangling:
        nl.warnings.append(f"소자가 하나만 연결된 노드: {', '.join(dangling)} (떠 있는 노드면 해석이 실패할 수 있음)")
    if len(nl.analyses) > 1:
        nl.warnings.append(f"해석 지시문이 {len(nl.analyses)}개입니다. 측정은 첫 해석({nl.analysis_kind})에 맞춥니다.")
    return nl


def set_component_value(text: str, name: str, value: float | str) -> str:
    """R/L/C 또는 DC 소스의 값을 바꾼 넷리스트 텍스트를 돌려준다(스윕용). 소자가 없으면 NetlistError."""
    lines = text.replace("\r\n", "\n").split("\n")
    target = name.lower()
    new = value if isinstance(value, str) else f"{value:.9g}"
    for i, ln in enumerate(lines):
        parts = ln.split()
        if not parts or parts[0].lower() != target:
            continue
        kind = parts[0][0].upper()
        if kind in "RLC" and len(parts) >= 4:
            parts[3] = new
        elif kind in "VI":
            low = [p.lower() for p in parts]
            if "dc" in low and low.index("dc") + 1 < len(parts):
                parts[low.index("dc") + 1] = new
            elif len(parts) >= 4 and re.match(r"^[-+.\d]", parts[3]):
                parts[3] = new
            else:
                raise NetlistError(f"{name}: 파형 소스(PULSE/SIN 등)의 값은 스윕할 수 없습니다. DC 값만 가능합니다.")
        else:
            raise NetlistError(f"{name}: R, L, C, DC 전압/전류원만 스윕할 수 있습니다.")
        lines[i] = " ".join(parts)
        return "\n".join(lines)
    raise NetlistError(f"소자 {name} 을(를) 넷리스트에서 찾지 못했습니다.")


_VEC_REF = re.compile(r"\b(v|vdb|vp|vm|vr|vi|i|idb|ip|im)\(\s*([^()\s,]+)\s*(?:,\s*([^()\s]+))?\s*\)", re.I)


def referenced_vectors(expr: str) -> list[tuple[str, str]]:
    """측정식에서 참조한 (함수, 노드/소스) 쌍. v(out) → ('v','out'), i(v1) → ('i','v1'), v(a,b) → 두 개."""
    out: list[tuple[str, str]] = []
    for m in _VEC_REF.finditer(expr):
        fn = m.group(1).lower()
        out.append((fn, m.group(2)))
        if m.group(3):
            out.append((fn, m.group(3)))
    return out
