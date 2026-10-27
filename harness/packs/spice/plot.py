"""파형·스윕 결과를 SVG 로 그린다. 외부 그래프 라이브러리 없이(의존성 0) 화면·README 에 바로 넣을 수 있는 벡터 그림."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence
from xml.sax.saxutils import escape

from .netlist import format_si

# 화면 디자인 토큰과 맞춘다(먹·결재 초록·도장 빨강). 배경은 투명해서 종이색 카드 위에 얹힌다.
INK, INK_SOFT, GRID = "#1B2430", "#5C6672", "#D9DEE3"
SERIES = ["#0E6B63", "#C8362B", "#B7791F", "#2F5D9E", "#6B4C9A", "#3A7D44", "#8A5A2B", "#444B55"]
FONT = "Pretendard, -apple-system, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif"


@dataclass
class Series:
    label: str
    x: Sequence[float]
    y: Sequence[float]


@dataclass
class Marker:
    """측정값 표시: x 세로선 또는 y 가로선과 라벨."""
    label: str
    x: float | None = None
    y: float | None = None


@dataclass
class Figure:
    series: list[Series]
    title: str = ""
    x_label: str = ""
    y_label: str = ""
    x_unit: str = ""
    y_unit: str = ""
    x_log: bool = False
    markers: list[Marker] = field(default_factory=list)
    width: int = 720
    height: int = 400


def _nice_ticks(lo: float, hi: float, n: int = 6) -> list[float]:
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=10) * mag
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while t <= hi + step * 1e-9:
        if t >= lo - step * 1e-9:
            ticks.append(round(t, 12))
        t += step
    return ticks


def _log_ticks(lo: float, hi: float) -> tuple[list[float], list[float]]:
    a, b = math.floor(math.log10(lo)), math.ceil(math.log10(hi))
    major = [10.0 ** e for e in range(a, b + 1) if lo <= 10.0 ** e <= hi]
    minor = [m * 10.0 ** e for e in range(a, b + 1) for m in (2, 5) if lo <= m * 10.0 ** e <= hi]
    return major, minor


def _fmt(v: float, unit: str) -> str:
    if unit:
        return format_si(v, unit, digits=3)
    if v == 0:
        return "0"
    if abs(v) >= 1000 or abs(v) < 0.01:
        return f"{v:.3g}"
    return f"{v:.4g}".rstrip("0").rstrip(".") if "." in f"{v:.4g}" else f"{v:.4g}"


def render_svg(fig: Figure) -> str:
    W, H = fig.width, fig.height
    left, right, top, bottom = 72, 20, 44 if fig.title else 20, 52
    if len(fig.series) > 1:
        top = max(top, 36)  # 범례는 그림 영역 밖(위쪽 오른쪽)에 둬 선·표시와 겹치지 않게 한다
    pw, ph = W - left - right, H - top - bottom
    xs = [x for s in fig.series for x in s.x if math.isfinite(x) and (not fig.x_log or x > 0)]
    ys = [y for s in fig.series for y in s.y if math.isfinite(y)]
    if not xs or not ys:
        raise ValueError("그릴 데이터가 없습니다.")
    xlo, xhi = min(xs), max(xs)
    ylo, yhi = min(ys), max(ys)
    for m in fig.markers:
        if m.y is not None:
            ylo, yhi = min(ylo, m.y), max(yhi, m.y)
    if yhi == ylo:
        ylo, yhi = ylo - 1, yhi + 1
    pad = (yhi - ylo) * 0.08
    ylo, yhi = ylo - pad, yhi + pad
    if fig.x_log:
        lxlo, lxhi = math.log10(xlo), math.log10(xhi)
        sx = lambda x: left + (math.log10(x) - lxlo) / (lxhi - lxlo or 1) * pw  # noqa: E731
    else:
        if xhi == xlo:
            xlo, xhi = xlo - 1, xhi + 1
        sx = lambda x: left + (x - xlo) / (xhi - xlo) * pw  # noqa: E731
    sy = lambda y: top + (yhi - y) / (yhi - ylo) * ph  # noqa: E731

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" font-family="{FONT}" font-size="12">']
    if fig.title:
        out.append(f'<text x="{left}" y="24" fill="{INK}" font-size="14" font-weight="600">{escape(fig.title)}</text>')
    # 격자·축
    if fig.x_log:
        major, minor = _log_ticks(xlo, xhi)
        for x in minor:
            out.append(f'<line x1="{sx(x):.1f}" y1="{top}" x2="{sx(x):.1f}" y2="{top + ph}" stroke="{GRID}" stroke-width="0.5" stroke-dasharray="2 3"/>')
        xticks = major
    else:
        xticks = _nice_ticks(xlo, xhi)
    for x in xticks:
        out.append(f'<line x1="{sx(x):.1f}" y1="{top}" x2="{sx(x):.1f}" y2="{top + ph}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{sx(x):.1f}" y="{top + ph + 18}" fill="{INK_SOFT}" text-anchor="middle">{escape(_fmt(x, fig.x_unit))}</text>')
    for y in _nice_ticks(ylo, yhi):
        out.append(f'<line x1="{left}" y1="{sy(y):.1f}" x2="{left + pw}" y2="{sy(y):.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{left - 8}" y="{sy(y) + 4:.1f}" fill="{INK_SOFT}" text-anchor="end">{escape(_fmt(y, fig.y_unit))}</text>')
    out.append(f'<rect x="{left}" y="{top}" width="{pw}" height="{ph}" fill="none" stroke="{INK_SOFT}" stroke-width="1"/>')
    if fig.x_label:
        out.append(f'<text x="{left + pw / 2:.1f}" y="{H - 10}" fill="{INK}" text-anchor="middle">{escape(fig.x_label)}</text>')
    if fig.y_label:
        out.append(f'<text transform="translate(14 {top + ph / 2:.1f}) rotate(-90)" fill="{INK}" text-anchor="middle">{escape(fig.y_label)}</text>')
    # 선
    for i, s in enumerate(fig.series):
        pts = [(sx(x), sy(y)) for x, y in zip(s.x, s.y) if math.isfinite(x) and math.isfinite(y) and (not fig.x_log or x > 0)]
        if not pts:
            continue
        d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
        color = SERIES[i % len(SERIES)]
        out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        if len(pts) <= 24:  # 점이 적은 스윕은 표식도 찍는다
            out.extend(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}" stroke="#fff" stroke-width="1.5"/>' for x, y in pts)
    # 측정 표시
    for m in fig.markers:
        if m.x is not None and (not fig.x_log or m.x > 0) and xlo <= m.x <= xhi:
            out.append(f'<line x1="{sx(m.x):.1f}" y1="{top}" x2="{sx(m.x):.1f}" y2="{top + ph}" stroke="{SERIES[1]}" stroke-width="1.2" stroke-dasharray="4 3"/>')
            out.append(f'<text x="{sx(m.x) + 5:.1f}" y="{top + 14}" fill="{SERIES[1]}" font-size="11">{escape(m.label)}</text>')
        if m.y is not None:
            out.append(f'<line x1="{left}" y1="{sy(m.y):.1f}" x2="{left + pw}" y2="{sy(m.y):.1f}" stroke="{SERIES[1]}" stroke-width="1.2" stroke-dasharray="4 3"/>')
            out.append(f'<text x="{left + pw - 4}" y="{sy(m.y) - 5:.1f}" fill="{SERIES[1]}" font-size="11" text-anchor="end">{escape(m.label)}</text>')
    # 범례: 그림 영역 위 오른쪽에 가로로
    if len(fig.series) > 1:
        x = left + pw
        ly = top - 10
        for i, s in reversed(list(enumerate(fig.series))):
            color = SERIES[i % len(SERIES)]
            w = 7 * len(s.label) + 30
            x -= w
            out.append(f'<line x1="{x}" y1="{ly - 4}" x2="{x + 18}" y2="{ly - 4}" stroke="{color}" stroke-width="2.5"/>')
            out.append(f'<text x="{x + 24}" y="{ly}" fill="{INK}" font-size="12">{escape(s.label)}</text>')
    out.append("</svg>")
    return "\n".join(out)


_X_UNITS = {"frequency": ("주파수", "Hz"), "time": ("시간", "s"), "v-sweep": ("전압", "V"), "i-sweep": ("전류", "A"), "temp-sweep": ("온도", "°C")}
_Y_UNITS = {"vdb": ("이득", "dB"), "vp": ("위상", "°"), "v": ("전압", "V"), "i": ("전류", "A")}


def figure_from_columns(cols: list[str], rows: list[list[float]], wanted: Sequence[str] | None = None, title: str = "",
                        x_log: bool | None = None, markers: Sequence[Marker] = ()) -> Figure:
    """wrdata 열(첫 열이 스케일) → Figure. wanted 로 그릴 열을 고른다(없으면 전부)."""
    if not cols or not rows:
        raise ValueError("데이터가 없습니다.")
    xcol = cols[0]
    xname, xunit = _X_UNITS.get(xcol.lower(), (xcol, ""))
    picks = [c for c in cols[1:] if not wanted or c.lower() in {w.lower() for w in wanted}]
    if not picks:
        raise ValueError(f"그릴 열이 없습니다. 있는 열: {cols[1:]}")
    fn = picks[0].split("(")[0].lower()
    yname, yunit = _Y_UNITS.get(fn, (picks[0], ""))
    if len({p.split("(")[0].lower() for p in picks}) > 1:
        yname, yunit = "값", ""
    idx = {c: i for i, c in enumerate(cols)}
    series = [Series(p, [r[0] for r in rows], [r[idx[p]] for r in rows]) for p in picks]
    if x_log is None:
        x_log = xcol.lower() == "frequency" and all(r[0] > 0 for r in rows)
    return Figure(series, title=title, x_label=f"{xname}" + (f" ({xunit})" if xunit else ""), y_label=f"{yname}" + (f" ({yunit})" if yunit else ""),
                  x_unit=xunit, y_unit=yunit, x_log=x_log, markers=list(markers))
