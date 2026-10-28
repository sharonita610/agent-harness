"""공정 NG 분석 비서의 "데이터" 탭: 최근 2주를 라인×공정 표, 일별 선, 급증 구간, 열린 티켓으로. 비서가 쓰는 것과 같은 기준(QA-005 중앙값 배수)."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ...tools.sql import connect_readonly, rows
from .seed import DEFAULT_DB, REFERENCE_DATE
from .tools import _shift

WINDOW_DAYS = 14


def _spikes(query, f: str, t: str, multiplier: float = 3.0, limit: int = 6) -> list[dict[str, Any]]:
    """detect_spikes 와 같은 규칙: 스테이션별 시간당 NG 율이 중앙값의 multiplier 배 이상인 연속 구간."""
    out = []
    for st in query("SELECT s.id, s.line_id, s.name FROM stations s ORDER BY s.id"):
        hours = query(
            "SELECT hour_start, inspected, ng, 1.0*ng/inspected AS rate FROM inspections WHERE station_id=? ORDER BY hour_start",
            (st["id"],),
        )
        base = statistics.median(h["rate"] for h in hours) if hours else 0
        if base <= 0:
            continue
        cur = None
        for h in hours:
            hot = f <= h["hour_start"] < t and h["inspected"] >= 50 and h["rate"] >= multiplier * base
            if not hot:
                continue
            if cur and datetime.strptime(h["hour_start"], "%Y-%m-%d %H:%M:%S") - datetime.strptime(
                cur["_last"], "%Y-%m-%d %H:%M:%S"
            ) == timedelta(hours=1):
                cur["_last"] = h["hour_start"]
                cur["hours"] += 1
                cur["ng"] += h["ng"]
                cur["inspected"] += h["inspected"]
            else:
                if cur:
                    out.append(cur)
                cur = {
                    "station": f"{st['line_id']}라인 {st['name']}",
                    "start": h["hour_start"],
                    "_last": h["hour_start"],
                    "hours": 1,
                    "ng": h["ng"],
                    "inspected": h["inspected"],
                    "base": base,
                }
        if cur:
            out.append(cur)
    for s in out:
        s["end"] = (datetime.strptime(s.pop("_last"), "%Y-%m-%d %H:%M:%S") + timedelta(hours=1)).strftime("%m-%d %H:%M")
        s["rate"] = round(100.0 * s["ng"] / s["inspected"], 2)
        s["multiplier"] = round(s["rate"] / (100 * s["base"]), 1)
        s["shift"] = _shift(int(s["start"][11:13]))
        s["start"] = s["start"][5:16]
    out.sort(key=lambda s: (s["multiplier"], s["hours"]), reverse=True)
    return [
        {
            "스테이션": s["station"],
            "시작": s["start"],
            "끝": s["end"],
            "시간": s["hours"],
            "교대": s["shift"],
            "NG율(%)": s["rate"],
            "배수": s["multiplier"],
        }
        for s in out[:limit]
    ]


def dashboard(scope: Any = None, db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    ro = connect_readonly(db_path)
    query = lambda sql, params=(): rows(ro.execute(sql, params))["rows"]
    t = datetime.combine(REFERENCE_DATE, datetime.min.time()) + timedelta(days=1)
    f = t - timedelta(days=WINDOW_DAYS)
    fs, ts = f.strftime("%Y-%m-%d %H:%M:%S"), t.strftime("%Y-%m-%d %H:%M:%S")
    tot = query(
        "SELECT SUM(inspected) AS i, SUM(ng) AS n FROM inspections WHERE hour_start>=? AND hour_start<?", (fs, ts)
    )[0]
    open_tickets = query("SELECT COUNT(*) AS n FROM actions WHERE status='open'")[0]["n"]
    unlabeled = query(
        "SELECT COUNT(*) AS n FROM ng_events WHERE label_status!='confirmed' AND ts>=? AND ts<?", (fs, ts)
    )[0]["n"]
    rules = query("SELECT COUNT(*) AS n FROM alert_rules WHERE enabled=1")[0]["n"]
    lines = query("SELECT id, name FROM lines ORDER BY id")
    procs = (
        [
            r["process"]
            for r in query("SELECT DISTINCT process FROM stations ORDER BY line_id, seq")
            if r["line_id"] == lines[0]["id"]
        ]
        if False
        else [r["name"] for r in query("SELECT name FROM stations WHERE line_id=? ORDER BY seq", (lines[0]["id"],))]
    )
    cell = {
        (r["line_id"], r["name"]): (100.0 * r["n"] / r["i"] if r["i"] else None)
        for r in query(
            "SELECT s.line_id, s.name, SUM(i.inspected) AS i, SUM(i.ng) AS n FROM inspections i JOIN stations s ON s.id=i.station_id "
            "WHERE i.hour_start>=? AND i.hour_start<? GROUP BY s.line_id, s.name",
            (fs, ts),
        )
    }
    heat = [[round(cell.get((ln["id"], pr), 0) or 0, 2) for pr in procs] for ln in lines]
    daily = query(
        "SELECT substr(hour_start,1,10) AS d, line_id, SUM(inspected) AS i, SUM(ng) AS n FROM inspections WHERE hour_start>=? AND hour_start<? GROUP BY d, line_id ORDER BY d",
        (fs, ts),
    )
    days = sorted({r["d"] for r in daily})
    by = {(r["d"], r["line_id"]): round(100.0 * r["n"] / r["i"], 2) if r["i"] else None for r in daily}
    defects = query(
        "SELECT COALESCE(confirmed_label, suggested_label, '미분류') AS d, COUNT(*) AS n FROM ng_events WHERE ts>=? AND ts<? GROUP BY 1 ORDER BY 2 DESC",
        (fs, ts),
    )
    tickets = query(
        "SELECT a.id AS 번호, substr(a.created_at,1,16) AS 생성, (s.line_id || '라인 ' || s.name) AS 스테이션, COALESCE(NULLIF(a.title,''), a.description) AS 내용, a.owner AS 담당, a.status AS 상태 "
        "FROM actions a JOIN stations s ON s.id=a.station_id ORDER BY a.status='open' DESC, a.created_at DESC LIMIT 8"
    )
    return [
        {
            "id": "stats",
            "title": f"최근 {WINDOW_DAYS}일 한눈에 ({f:%m-%d} ~ {REFERENCE_DATE:%m-%d})",
            "kind": "stats",
            "span": 2,
            "data": [
                {"label": "검사 수", "value": tot["i"] or 0, "unit": "개"},
                {"label": "NG 수", "value": tot["n"] or 0, "unit": "개"},
                {"label": "NG율", "value": round(100.0 * (tot["n"] or 0) / (tot["i"] or 1), 2), "unit": "%"},
                {
                    "label": "열린 조치 티켓",
                    "value": open_tickets,
                    "unit": "건",
                    "tone": "warn" if open_tickets else "ok",
                },
                {"label": "미확정 라벨", "value": unlabeled, "unit": "건"},
                {"label": "켜진 경보 규칙", "value": rules, "unit": "개"},
            ],
        },
        {
            "id": "heat",
            "title": "라인 × 공정 NG율 (%)",
            "kind": "heat",
            "data": {"rows": [f"{ln['id']}라인" for ln in lines], "cols": procs, "values": heat, "unit": "%"},
            "note": "색이 진할수록 NG율이 높다. 비서의 '어디서 튀었나' 답을 여기서 대조한다.",
        },
        {
            "id": "daily",
            "title": "일별 NG율 (%) 라인별",
            "kind": "line",
            "span": 2,
            "data": {
                "x": [d[5:] for d in days],
                "series": [
                    {"name": f"{ln['id']}라인", "values": [by.get((d, ln["id"])) for d in days]} for ln in lines
                ],
                "unit": "%",
            },
        },
        {
            "id": "spikes",
            "title": "급증 구간 (기준치의 3배 이상, 배수 순)",
            "kind": "table",
            "data": {
                "columns": ["스테이션", "시작", "끝", "시간", "교대", "NG율(%)", "배수"],
                "rows": _spikes(query, fs, ts),
            },
            "note": "기준치 = 스테이션의 최근 2주 시간당 NG율 중앙값(QA-005).",
        },
        {
            "id": "defects",
            "title": "불량 유형별 NG 수",
            "kind": "bar",
            "data": {
                "x": [r["d"] for r in defects],
                "series": [{"name": "NG 수", "values": [r["n"] for r in defects]}],
                "unit": "개",
            },
        },
        {
            "id": "tickets",
            "title": "조치 티켓",
            "kind": "table",
            "span": 2,
            "data": {"columns": ["번호", "생성", "스테이션", "내용", "담당", "상태"], "rows": tickets},
        },
    ]
