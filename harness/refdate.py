"""데이터 기준일: 비서가 "오늘" 로 삼는 날짜 (ADR-034).

합성 시드는 날짜를 고정해 "지난달" 같은 상대 표현이 평가에서 흔들리지 않게 한다(각 팩 seed.REFERENCE_DATE).
실데이터는 스냅샷을 찍은 시각이 기준이라 그 값을 주입해야 한다. 우선순위:
  1. 환경변수 HARNESS_REFERENCE_DATE = "YYYY-MM-DD" | "today"
  2. DB 의 _meta 표(key='reference_date') — 스냅샷 스크립트가 write_reference_date 로 적는다
  3. 팩 기본(시드 날짜)
프롬프트("오늘은 …, 지난달은 …")·도구("오늘까지의 지연 일수")·평가 문제({last_month} 같은 변수)가 전부 이 하나를 본다.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ENV = "HARNESS_REFERENCE_DATE"
META_TABLE = "_meta"
META_KEY = "reference_date"
DAYS_BACK = 60  # 평가 변수 ref_date-N 의 N 범위(0~60일 전)
MONTHS_BACK = 12  # month-N 의 N 범위(0~12달 전)


def resolve_reference_date(default: date, db_path: Path | str | None = None) -> tuple[date, str]:
    """(기준일, 출처). 출처는 "env" | "env:today" | "db" | "default". 잘못된 값은 조용히 넘기지 않고 ValueError."""
    raw = os.getenv(ENV, "").strip()
    if raw:
        if raw.lower() == "today":
            return date.today(), "env:today"
        try:
            return date.fromisoformat(raw), "env"
        except ValueError as e:
            raise ValueError(f"{ENV} 는 YYYY-MM-DD 또는 today 여야 합니다(받은 값: {raw!r})") from e
    if db_path is not None and Path(db_path).exists():
        row = None
        try:
            conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
            try:
                row = conn.execute(f"SELECT value FROM {META_TABLE} WHERE key=?", (META_KEY,)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            row = None  # _meta 표가 없는 DB(합성 시드)는 기본값으로
        if row and row[0]:
            try:
                return date.fromisoformat(str(row[0])[:10]), "db"
            except ValueError as e:
                raise ValueError(f"{db_path} 의 {META_TABLE}.{META_KEY} 가 날짜가 아닙니다: {row[0]!r}") from e
    return default, "default"


def write_reference_date(db_path: Path | str, ref: date) -> None:
    """스냅샷 스크립트용: DB 에 기준일을 적는다(있으면 덮어씀). 비서·평가가 이 값을 "오늘" 로 쓴다."""
    conn = sqlite3.connect(str(db_path))
    try:
        with conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {META_TABLE}(key TEXT PRIMARY KEY, value TEXT)")
            conn.execute(f"INSERT OR REPLACE INTO {META_TABLE}(key, value) VALUES (?, ?)", (META_KEY, ref.isoformat()))
    finally:
        conn.close()


def month_start(d: date) -> date:
    return d.replace(day=1)


def shift_months(d: date, months: int) -> date:
    """달 단위 이동, 결과는 그 달 1일. 연말·연초를 넘어도 맞는다(2027-01 의 지난달은 2026-12)."""
    y, m = divmod(d.year * 12 + (d.month - 1) + months, 12)
    return date(y, m + 1, 1)


def previous_month(d: date) -> date:
    return shift_months(d, -1)


def month_label(d: date) -> str:
    return f"{d.year}년 {d.month}월"


def date_vars(ref: date) -> dict[str, str]:
    """평가 태스크의 {변수}. 질문·turns·truth_sql·params_sql·정규식 어디서든 쓴다(runner._fill 이 그대로 치환).
    ref_date · ref_year · this_month(YYYY-MM) · this_month_start · this_month_label(YYYY년 M월) · this_month_short(M월)
    · last_month · last_month_start · last_month_label · last_month_short · next_month_start
    · month-N / month-N_label / month-N_short (N달 전, 0~12) · ref_date-N (N일 전 YYYY-MM-DD, 0~60) · ref_date-N_md (MM-DD) · ref_date-N_ko (M월 D일)"""
    this_m, last_m = month_start(ref), previous_month(ref)
    out: dict[str, str] = {
        "ref_date": ref.isoformat(),
        "ref_year": str(ref.year),
        "this_month": f"{this_m:%Y-%m}",
        "this_month_start": this_m.isoformat(),
        "this_month_label": month_label(this_m),
        "this_month_short": f"{this_m.month}월",
        "last_month": f"{last_m:%Y-%m}",
        "last_month_start": last_m.isoformat(),
        "last_month_label": month_label(last_m),
        "last_month_short": f"{last_m.month}월",
        "next_month_start": shift_months(ref, 1).isoformat(),
    }
    for n in range(MONTHS_BACK + 1):
        m = shift_months(ref, -n)
        out[f"month-{n}"] = f"{m:%Y-%m}"
        out[f"month-{n}_label"] = month_label(m)
        out[f"month-{n}_short"] = f"{m.month}월"
    for n in range(DAYS_BACK + 1):
        d = ref - timedelta(days=n)
        out[f"ref_date-{n}"] = d.isoformat()
        out[f"ref_date-{n}_md"] = f"{d:%m-%d}"
        out[f"ref_date-{n}_ko"] = f"{d.month}월 {d.day}일"
    return out
