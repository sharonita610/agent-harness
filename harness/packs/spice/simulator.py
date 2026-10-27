"""ngspice 배치 실행과 결과 파싱. 외부 프로그램(ngspice) 위에서 에이전트가 쓰는 '전문 소프트웨어 어댑터'.

- 넷리스트는 netlist.parse_netlist 로 가드·파싱한 뒤, 제어 블록(.control ~ .endc)은 이 모듈이 직접 만든다.
  모델이 준 측정식은 글자 허용 목록으로 걸러 제어 블록 주입을 막는다.
- 실행은 `ngspice -n -b` 서브프로세스, 시간 제한, stdin 차단. 결과 파일은 data/spice_runs/<run_id>/ 에 남겨 그래프·재조회에 쓴다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .netlist import Netlist, NetlistError, parse_netlist, parse_si, referenced_vectors, set_component_value

ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = Path(os.getenv("HARNESS_SPICE_RUNS", str(ROOT / "data" / "spice_runs")))
MAX_POINTS = 2_000_000       # 해석 한 번이 만들 수 있는 데이터 점수 상한(메모리·시간 보호)
MAX_SAVE_VECTORS = 8
MAX_SWEEP_POINTS = 60

_MEAS_TAIL = re.compile(r"^[\w\s.,=+\-*/()\[\]<>#']*$")
_MEAS_FORMS = ("when", "find", "max", "min", "avg", "rms", "pp", "integ", "deriv", "trig", "param", "max_at", "min_at", "at")
_MEAS_KINDS = ("ac", "dc", "tran", "op", "sp")
_RESULT_LINE = re.compile(r"^\s*([A-Za-z_][\w#]*)\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
_ROWS_LINE = re.compile(r"No\. of Data Rows\s*:\s*(\d+)")
_DISPLAY_LINE = re.compile(r"^\s{2,}(\S+)\s*:\s*(\w+),\s*(real|complex),\s*(\d+) long")


class SimulationError(RuntimeError):
    pass


def ngspice_path() -> str | None:
    return os.getenv("HARNESS_NGSPICE") or shutil.which("ngspice")


def ngspice_available() -> bool:
    return ngspice_path() is not None


_version_cache: dict[str, str | None] = {}


def ngspice_version() -> str | None:
    if "v" not in _version_cache:
        exe = ngspice_path()
        ver = None
        if exe:
            try:
                out = subprocess.run([exe, "-v"], capture_output=True, text=True, timeout=10).stdout
                m = re.search(r"ngspice-(\d+(?:\.\d+)*)", out)
                ver = m.group(0) if m else out.strip().splitlines()[0][:40]
            except Exception:
                ver = None
        _version_cache["v"] = ver
    return _version_cache["v"]


# ----------------------------------------------------------------------------- 측정식
def normalize_measurement(spec: str, analysis_kind: str) -> tuple[str, str, str | None]:
    """'ac fc WHEN vdb(out)=-3' | 'meas tran tau WHEN v(out)=0.632' | 'fc WHEN vdb(out)=-3' → (이름, 제어 블록 한 줄, 경고).

    해석 종류가 넷리스트와 다르면 넷리스트 쪽으로 고친다(ngspice 의 meas 는 해석 종류가 맞아야 값을 낸다).
    """
    if any(ch in spec for ch in "\n\r;"):
        raise NetlistError("측정식에는 줄바꿈이나 ';' 를 쓸 수 없습니다(한 줄에 하나).")
    s = " ".join(spec.split())
    if not s:
        raise NetlistError("빈 측정식")
    tokens = s.split()
    if tokens[0].lower() in (".meas", ".measure", "meas", "measure"):
        tokens = tokens[1:]
    warn = None
    kind = analysis_kind
    if tokens and tokens[0].lower() in _MEAS_KINDS:
        if tokens[0].lower() != analysis_kind:
            warn = f"측정식의 해석 종류 '{tokens[0]}' 를 넷리스트의 '{analysis_kind}' 로 바꿨습니다."
        tokens = tokens[1:]
    if len(tokens) < 2:
        raise NetlistError(f"측정식 형식 오류: {spec!r}. 예: 'fc WHEN vdb(out)=-3', 'g1k FIND vdb(out) AT=1000', 'vmax MAX v(out)'")
    name, rest = tokens[0], " ".join(tokens[1:])
    if not re.fullmatch(r"[A-Za-z_]\w{0,30}", name):
        raise NetlistError(f"측정 이름은 영문자로 시작하는 식별자여야 합니다: {name!r}")
    if not _MEAS_TAIL.fullmatch(rest) or rest.split()[0].lower() not in _MEAS_FORMS:
        raise NetlistError(f"측정식에 허용되지 않는 형식/문자: {rest!r}. WHEN / FIND … AT= / MAX / MIN / AVG / RMS / PP / TRIG … TARG … 만 쓸 수 있습니다.")
    if kind == "op":
        raise NetlistError(".op 해석에는 측정식을 쓸 수 없습니다. 노드 전압·전류는 자동으로 모두 출력됩니다.")
    return name, f"meas {kind} {name} {rest}", warn


def _check_refs(exprs: Iterable[str], nl: Netlist) -> list[str]:
    """측정식·저장 벡터가 가리키는 노드/소스가 넷리스트에 있는지. 없는 것 목록."""
    nodes = {n.lower() for n in nl.nodes}
    sources = {c.name.lower() for c in nl.components if c.kind in "VI"}
    missing: list[str] = []
    for e in exprs:
        for fn, ref in referenced_vectors(e):
            if fn.startswith("i"):
                if ref.lower() not in sources:
                    missing.append(f"{fn}({ref}) → 전류는 전압원 이름으로만 잴 수 있습니다(예: i(V1)). 있는 전압원: {sorted(sources) or '없음'}")
            elif ref.lower() not in nodes:
                missing.append(f"{fn}({ref}) → 노드 '{ref}' 없음. 있는 노드: {nl.nodes}")
    return missing


def estimate_points(analysis: str) -> int | None:
    """해석 지시문이 만들 데이터 점수 추정(.tran, .ac, .dc). 모르면 None."""
    p = analysis.split()
    kind = p[0].lower()
    try:
        if kind == ".tran" and len(p) >= 3:
            step, stop = parse_si(p[1]), parse_si(p[2])
            return int(stop / step) if step > 0 else None
        if kind == ".ac" and len(p) >= 5:
            n, f1, f2 = int(parse_si(p[2])), parse_si(p[3]), parse_si(p[4])
            if p[1].lower() == "lin":
                return n
            import math
            return int(n * max(1.0, math.log10(f2 / f1))) if p[1].lower() == "dec" else int(n * max(1.0, math.log2(f2 / f1)))
        if kind == ".dc" and len(p) >= 5:
            start, stop, step = parse_si(p[2]), parse_si(p[3]), parse_si(p[4])
            return int(abs(stop - start) / abs(step)) + 1 if step else None
    except (NetlistError, ValueError, ZeroDivisionError):
        return None
    return None


# ----------------------------------------------------------------------------- 실행
def _run(cir: str, workdir: Path, timeout_s: float) -> tuple[int, str, float]:
    exe = ngspice_path()
    if not exe:
        raise SimulationError("ngspice 가 설치되어 있지 않습니다. macOS: brew install ngspice · Debian: apt-get install ngspice")
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "circuit.cir").write_text(cir, encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/opt/homebrew/bin"), "HOME": str(workdir), "LANG": "C"}
    t0 = time.monotonic()
    try:
        r = subprocess.run([exe, "-n", "-b", "circuit.cir"], cwd=str(workdir), capture_output=True, text=True,
                           timeout=timeout_s, stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        (workdir / "output.log").write_text(out, encoding="utf-8")
        raise SimulationError(f"시뮬레이션 시간 초과({timeout_s:.0f}s). 해석 범위(.tran 의 tstop/tstep, .ac 의 점수)를 줄여 보세요.") from None
    elapsed = time.monotonic() - t0
    out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
    (workdir / "output.log").write_text(out, encoding="utf-8")
    return r.returncode, out, elapsed


def parse_output(out: str) -> dict[str, Any]:
    results: dict[str, float] = {}
    vectors: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    points = None
    for ln in out.splitlines():
        if m := _ROWS_LINE.search(ln):
            points = int(m.group(1))
            continue
        if m := _DISPLAY_LINE.match(ln):
            vectors.append({"name": m.group(1), "type": m.group(2), "complex": m.group(3) == "complex", "points": int(m.group(4))})
            continue
        low = ln.lower().strip()
        if low.startswith(("error", "fatal")) or "singular matrix" in low or "timestep too small" in low or "failed" in low:
            errors.append(ln.strip())
            continue
        if low.startswith("warning") and "compatibility" not in low:
            warnings.append(ln.strip())
            continue
        if m := _RESULT_LINE.match(ln):
            results[m.group(1)] = float(m.group(2))
    return {"results": results, "vectors": vectors, "errors": errors, "warnings": warnings, "points": points}


def _control_block(nl: Netlist, meas_lines: list[str], save: list[str], data_file: str = "data.txt") -> str:
    lines = [".control", "set filetype=ascii", "set wr_singlescale", "set wr_vecnames", "set units=degrees", "run", "display"]
    if nl.analysis_kind == "op":
        lines.append("print all")
    lines += meas_lines
    if save:
        lines.append(f"wrdata {data_file} " + " ".join(save))
    lines += ["quit", ".endc"]
    return "\n".join(lines)


def _default_save(nl: Netlist, measurements: list[str], requested: list[str]) -> list[str]:
    kind = nl.analysis_kind
    if kind == "op":
        return []
    save: list[str] = []

    def add(expr: str) -> None:
        e = expr.strip()
        if e and e not in save and len(save) < MAX_SAVE_VECTORS:
            save.append(e)

    for r in requested:
        if kind == "ac" and re.fullmatch(r"v\((.+)\)", r, re.I):
            inner = re.fullmatch(r"v\((.+)\)", r, re.I).group(1)
            add(f"vdb({inner})"); add(f"vp({inner})")
        else:
            add(r)
    if not requested:
        for n in nl.nodes:
            if n == "0":
                continue
            add(f"vdb({n})" if kind == "ac" else f"v({n})")
        for m in measurements:
            for fn, ref in referenced_vectors(m):
                add(f"{fn}({ref})")
    return save


def simulate(netlist_text: str, measurements: Iterable[str] = (), save_vectors: Iterable[str] = (), runs_dir: Path = RUNS_DIR,
             timeout_s: float = 20.0, run_id: str | None = None) -> dict[str, Any]:
    """넷리스트 + 측정식 → {run_id, analysis, points, measurements, vectors, warnings, errors, elapsed_ms, data_file}."""
    nl = parse_netlist(netlist_text)
    if not nl.analyses:
        raise NetlistError("해석 지시문이 없습니다. .op / .dc / .ac / .tran 중 하나를 넷리스트에 넣으세요. 예: '.ac dec 20 10 1meg'")
    est = estimate_points(nl.analyses[0])
    if est and est > MAX_POINTS:
        raise NetlistError(f"해석이 너무 큽니다(약 {est:,} 점 > {MAX_POINTS:,}). 시간 간격을 키우거나 범위를 줄이세요: {nl.analyses[0]}")
    meas_lines: list[str] = []
    meas_names: list[str] = []
    warnings = list(nl.warnings)
    specs = [*measurements, *nl.inline_measurements]
    if nl.analysis_kind == "op" and specs:
        warnings.append(f"동작점(.op) 해석은 측정식 없이 모든 노드 전압과 전원 전류를 돌려주므로 측정식 {len(specs)}개는 무시했습니다.")
        specs = []
    inline_names = {m.split()[1 if m.split()[0].lower() in _MEAS_KINDS else 0].lower() for m in nl.inline_measurements if len(m.split()) >= 2}
    for spec in specs:
        if len(spec.split()) == 1 and spec.strip().lower() in inline_names:
            warnings.append(f"measurements 의 '{spec.strip()}' 은 넷리스트 .measure 와 같은 이름이라 한 번만 잽니다.")
            continue
        if len(spec.split()) == 1:
            raise NetlistError(f"측정식 '{spec.strip()}' 에 이름만 있습니다. '이름 형식 …' 전체를 적으세요. 예: '{spec.strip()} WHEN v(out)=0.632'. "
                               "넷리스트 안의 .measure 줄은 자동으로 반영되니 그럴 땐 measurements 를 비워 두세요.")
        name, line, warn = normalize_measurement(spec, nl.analysis_kind or "")
        meas_names.append(name)
        meas_lines.append(line)
        if warn:
            warnings.append(warn)
    requested = [s for s in save_vectors if s and s.strip()]
    missing = _check_refs([*meas_lines, *requested], nl)
    if missing:
        raise NetlistError("참조 오류: " + " / ".join(missing))
    save = _default_save(nl, meas_lines, requested)

    rid = run_id or "sim_" + uuid.uuid4().hex[:8]
    workdir = Path(runs_dir) / rid
    cir = nl.text() + "\n\n" + _control_block(nl, meas_lines, save) + "\n.end\n"
    rc, out, elapsed = _run(cir, workdir, timeout_s)
    parsed = parse_output(out)
    if nl.analysis_kind == "op":
        measured = {k: v for k, v in parsed["results"].items()}
    else:
        measured = {n: parsed["results"].get(n) for n in meas_names}
    failed = [n for n, v in measured.items() if v is None]
    if failed:
        warnings.append(f"값이 나오지 않은 측정: {failed}. 조건을 만족하는 점이 없거나(예: 범위 안에서 -3dB 를 지나지 않음) 식이 틀렸을 수 있습니다. get_waveform 으로 데이터를 직접 보세요.")
    if rc != 0 and not parsed["results"]:
        parsed["errors"].append(f"ngspice 종료 코드 {rc}")
    result = {
        "run_id": rid,
        "analysis": nl.analyses[0],
        "analysis_kind": nl.analysis_kind,
        "points": parsed["points"],
        "measurements": measured,
        "vectors": [v["name"] for v in parsed["vectors"]],
        "saved_vectors": save,
        "warnings": warnings + parsed["warnings"],
        "errors": parsed["errors"],
        "elapsed_ms": round(elapsed * 1000),
        "ngspice": ngspice_version(),
    }
    (workdir / "meta.json").write_text(json.dumps({**result, "title": nl.title, "components": nl.summary()["components"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    if parsed["errors"] and not parsed["results"] and parsed["points"] in (None, 0):
        raise SimulationError("시뮬레이션 실패: " + " / ".join(parsed["errors"][:3]))
    return result


def load_data(run_dir: Path, max_points: int | None = None) -> tuple[list[str], list[list[float]]]:
    """wrdata 출력(ascii, 단일 스케일, 이름 줄 포함) → (열 이름, 행). max_points 를 주면 균등 간격으로 줄인다."""
    f = Path(run_dir) / "data.txt"
    if not f.exists():
        raise FileNotFoundError(f"저장된 파형이 없습니다: {run_dir}")
    lines = [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return [], []
    cols = lines[0].split()
    rows: list[list[float]] = []
    for ln in lines[1:]:
        try:
            rows.append([float(x) for x in ln.split()])
        except ValueError:
            continue
    if max_points and len(rows) > max_points:
        step = (len(rows) - 1) / (max_points - 1)
        rows = [rows[round(i * step)] for i in range(max_points)]
    return cols, rows


def run_meta(run_dir: Path) -> dict[str, Any]:
    f = Path(run_dir) / "meta.json"
    if not f.exists():
        raise FileNotFoundError(f"실행 기록이 없습니다: {run_dir}")
    return json.loads(f.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------- 스윕
def sweep(netlist_text: str, component: str, values: list[float], measurement: str, runs_dir: Path = RUNS_DIR,
          timeout_s_total: float = 120.0, run_id: str | None = None) -> dict[str, Any]:
    """소자 값을 바꾸며 같은 측정을 반복. 결과는 표 + CSV(data.txt) 로 남겨 plot_waveform 이 그릴 수 있게 한다."""
    if not values:
        raise NetlistError("스윕 값이 비어 있습니다.")
    if len(values) > MAX_SWEEP_POINTS:
        raise NetlistError(f"스윕 점수가 너무 많습니다({len(values)} > {MAX_SWEEP_POINTS}).")
    nl = parse_netlist(netlist_text)
    if not nl.analyses:
        raise NetlistError("해석 지시문이 없습니다(.ac/.dc/.tran).")
    name, _, _ = normalize_measurement(measurement, nl.analysis_kind or "")
    rid = run_id or "swp_" + uuid.uuid4().hex[:8]
    base = Path(runs_dir) / rid
    base.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    rows: list[dict[str, Any]] = []
    per_run_timeout = max(2.0, timeout_s_total / max(1, len(values)))
    for i, v in enumerate(values):
        remaining = timeout_s_total - (time.monotonic() - t0)
        if remaining <= 0:
            rows.append({"value": v, name: None, "error": "전체 시간 초과"})
            break
        text = set_component_value(nl.text(), component, v)
        try:
            r = simulate(text, [measurement], save_vectors=[], runs_dir=base, timeout_s=min(per_run_timeout, remaining), run_id=f"p{i:03d}")
            rows.append({"value": v, name: r["measurements"].get(name)})
        except (NetlistError, SimulationError) as e:
            rows.append({"value": v, name: None, "error": str(e)[:160]})
    elapsed = time.monotonic() - t0
    with (base / "data.txt").open("w", encoding="utf-8") as f:
        f.write(f" {component} {name}\n")
        for r in rows:
            if r.get(name) is not None:
                f.write(f" {r['value']:.9e} {r[name]:.9e}\n")
    result = {"run_id": rid, "kind": "sweep", "component": component, "measurement": name, "analysis": nl.analyses[0],
              "analysis_kind": nl.analysis_kind, "points": len(rows), "failed": sum(1 for r in rows if r.get(name) is None),
              "rows": rows, "elapsed_ms": round(elapsed * 1000)}
    (base / "meta.json").write_text(json.dumps({**result, "title": nl.title}, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def sweep_values(start: float | None, stop: float | None, points: int | None, log: bool, values: list[float] | None) -> list[float]:
    if values:
        return [float(v) for v in values]
    if start is None or stop is None or not points:
        raise NetlistError("values 또는 (start, stop, points) 를 주세요.")
    if points < 2:
        return [float(start)]
    if log:
        import math
        if start <= 0 or stop <= 0:
            raise NetlistError("로그 스윕은 양수 범위만 가능합니다.")
        a, b = math.log10(start), math.log10(stop)
        return [10 ** (a + (b - a) * i / (points - 1)) for i in range(points)]
    return [start + (stop - start) * i / (points - 1) for i in range(points)]
