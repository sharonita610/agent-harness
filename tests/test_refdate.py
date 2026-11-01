"""데이터 기준일 주입(ADR-034): 환경변수 > DB _meta > 시드. 프롬프트·도구·평가 변수가 한 값을 본다."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import date

import pytest

from harness.refdate import (
    ENV,
    date_vars,
    month_label,
    previous_month,
    resolve_reference_date,
    shift_months,
    write_reference_date,
)

SEED = date(2026, 10, 27)


def test_resolve_priority_env_db_default(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert resolve_reference_date(SEED, None) == (SEED, "default")
    db = tmp_path / "x.db"
    sqlite3.connect(db).execute("CREATE TABLE t(x)").connection.close()
    assert resolve_reference_date(SEED, db) == (SEED, "default")  # _meta 없는 DB(합성 시드)
    write_reference_date(db, date(2026, 11, 2))
    assert resolve_reference_date(SEED, db) == (date(2026, 11, 2), "db")
    write_reference_date(db, date(2026, 11, 3))  # 덮어쓰기
    assert resolve_reference_date(SEED, db)[0] == date(2026, 11, 3)
    monkeypatch.setenv(ENV, "2027-01-15")
    assert resolve_reference_date(SEED, db) == (date(2027, 1, 15), "env")  # 환경변수가 DB 보다 먼저
    monkeypatch.setenv(ENV, "today")
    assert resolve_reference_date(SEED, db) == (date.today(), "env:today")
    monkeypatch.setenv(ENV, "어제")
    with pytest.raises(ValueError, match=ENV):
        resolve_reference_date(SEED, db)


def test_month_math_crosses_year_boundary():
    assert previous_month(date(2027, 1, 15)) == date(2026, 12, 1)
    assert shift_months(date(2026, 10, 27), -3) == date(2026, 7, 1)
    assert shift_months(date(2026, 12, 31), 1) == date(2027, 1, 1)
    assert month_label(date(2026, 9, 1)) == "2026년 9월"


def test_date_vars_match_the_old_hardcoded_values():
    """시드 기준일(2026-10-27)로 렌더하면 예전에 yaml 에 박혀 있던 글자와 똑같아야 한다(평가 결과 비교 가능성)."""
    v = date_vars(SEED)
    assert v["ref_date"] == "2026-10-27" and v["last_month"] == "2026-09" and v["this_month"] == "2026-10"
    assert v["last_month_start"] == "2026-09-01" and v["this_month_start"] == "2026-10-01"
    assert (
        v["last_month_label"] == "2026년 9월" and v["month-3_label"] == "2026년 7월" and v["this_month_short"] == "10월"
    )
    assert v["ref_date-9"] == "2026-10-18" and v["ref_date-9_md"] == "10-18" and v["ref_date-9_ko"] == "10월 18일"
    assert v["next_month_start"] == "2026-11-01"
    jan = date_vars(date(2027, 1, 15))
    assert jan["last_month"] == "2026-12" and jan["last_month_label"] == "2026년 12월"


def test_enterprise_prompt_and_tools_follow_the_reference_date(seeded_db):
    from harness.packs.enterprise.tools import build_tools, system_prompts_for
    from harness.tools.registry import ToolRegistry

    p = system_prompts_for(date(2027, 1, 15))
    assert "2027-01-15" in p["v1"] and '"지난달" 은 2026-12' in p["v1"] and "2026-12" in p["v2"]
    assert "예정 01-04, 오늘 01-15 이면 11일" in p["v1"]  # 지연 일수 예시도 기준일에서 나온다
    db, _ = seeded_db
    oid = (
        sqlite3.connect(db)
        .execute(
            "SELECT o.id FROM orders o JOIN shipments s ON s.order_id=o.id WHERE o.status='지연' AND s.shipped_date IS NULL ORDER BY o.id LIMIT 1"
        )
        .fetchone()[0]
    )
    base = ToolRegistry(build_tools(db, reference_date=SEED)).get("get_order").run({"order_id": oid})
    later = ToolRegistry(build_tools(db, reference_date=date(2026, 11, 6))).get("get_order").run({"order_id": oid})
    assert base["reference_date"] == "2026-10-27" and later["reference_date"] == "2026-11-06"
    assert later["delay_days"] == base["delay_days"] + 10  # 미출고 지연 주문의 지연 일수는 "오늘" 까지


def test_enterprise_build_tools_reads_db_meta(seeded_db, tmp_path):
    from harness.packs.enterprise.tools import build_tools
    from harness.tools.registry import ToolRegistry

    db, _ = seeded_db
    copy = tmp_path / "snapshot.db"
    shutil.copy(db, copy)
    write_reference_date(copy, date(2026, 11, 2))
    out = ToolRegistry(build_tools(copy)).get("get_order").run({"order_id": 1})
    assert out["reference_date"] == "2026-11-02"


def test_factory_prompt_follows_reference_date_and_data_period():
    from harness.packs.factory.tools import system_prompts_for

    p = system_prompts_for(date(2026, 11, 2), period=("2026-10-14 00:00:00", "2026-10-28 00:00:00"))["v1"]
    assert "오늘 날짜: 2026-11-02." in p and "데이터 기간은 2026-10-14 ~ 2026-10-27 (14일)" in p


def test_eval_prepare_fills_date_vars_and_refuses_unfilled(seeded_db):
    import yaml

    from harness.evals.runner import prepare
    from harness.packs.enterprise.tools import DOCS_DIR

    db, _ = seeded_db
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tasks = {
        t["id"]: t for t in yaml.safe_load((DOCS_DIR.parents[2] / "evals" / "tasks.yaml").read_text(encoding="utf-8"))
    }
    task, q, truth = prepare(tasks["q02"], conn, date_vars(SEED))
    assert q == "지난달(2026년 9월) 주문 건수는 몇 건이야? 취소된 주문은 빼고."
    assert truth == list(
        conn.execute(
            "SELECT COUNT(*) FROM orders WHERE order_date >= '2026-09-01' AND order_date < '2026-10-01' AND status != '취소'"
        ).fetchone()
    )
    _, q_nov, truth_nov = prepare(tasks["q02"], conn, date_vars(date(2026, 11, 2)))
    assert "지난달(2026년 10월)" in q_nov and truth_nov != truth  # 기준일을 옮기면 질문·정답이 같이 움직인다
    with pytest.raises(ValueError, match="q02.*last_month"):
        prepare(tasks["q02"], conn)  # 변수를 안 주면 조용히 틀린 SQL 을 돌리지 않고 거절
