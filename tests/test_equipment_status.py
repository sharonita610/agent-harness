"""설비 상태 도구(공정 팩)와 시뮬레이터 상태 도구(회로 팩), ADR-026."""

from __future__ import annotations

import re

import pytest

from harness.packs.factory.tools import DOCS_DIR, PARAM_RANGES, build_tools
from harness.tools.registry import ToolRegistry


def test_param_ranges_match_document_pe003():
    """코드의 관리 범위 표가 문서 PE-003 의 표와 같아야 한다(문서를 고치면 테스트가 잡는다)."""
    text = (DOCS_DIR / "03-공정별-관리-파라미터.md").read_text(encoding="utf-8")
    name_to_proc = {
        "전극 코팅": "coating",
        "슬리팅": "slitting",
        "권취": "winding",
        "조립": "assembly",
        "전해액 주입": "filling",
        "화성": "formation",
    }
    found = {}
    for m in re.finditer(r"^\| (\S+(?: \S+)?) \| (\w+) \| [^|]+ \| (\d+) ~ (\d+) \|", text, re.M):
        found.setdefault(name_to_proc[m.group(1)], {})[m.group(2)] = (int(m.group(3)), int(m.group(4)))
    assert found == {p: {k: (lo, hi) for k, (lo, hi, _) in v.items()} for p, v in PARAM_RANGES.items()}


@pytest.fixture(scope="module")
def reg(factory_db):
    db, _ = factory_db
    return ToolRegistry(build_tools(db_path=db))


def test_equipment_status_flags_heater_anomaly_window(reg):
    r = reg.get("equipment_status").run(
        {"station": "2라인 권취", "from_ts": "2026-10-18 09:00", "to_ts": "2026-10-18 14:00"}
    )
    temp = next(p for p in r["params"] if p["param"] == "temp_c")
    assert r["station"] == "2라인 권취(S23)" and temp["status"] == "높음" and temp["range"] == [22, 27]
    assert 30 < temp["window_mean"] < 34 and r["out_of_range"] == ["temp_c"] and r["verdict"] == "주의"
    assert r["maintenance_recent"][0]["kind"] == "히터 교체" and r["equipment"]["days_since_maintenance"] == 9
    assert any("관리 범위" in i for i in r["issues"])


def test_equipment_status_default_window_is_last_24h_and_normal(reg):
    r = reg.get("equipment_status").run({"station": "45"})
    assert r["window"] == ["2026-10-27 00:00:00", "2026-10-28 00:00:00"]
    assert all(p["status"] == "정상" for p in r["params"]) and r["out_of_range"] == []
    assert r["equipment"]["days_since_maintenance"] == 33 and any("30일 초과" in i for i in r["issues"])
    with pytest.raises(ValueError, match="하나만"):
        reg.get("equipment_status").run({"station": "권취"})  # 4개 라인에 걸림


def test_list_equipment_counts_overdue_maintenance(reg):
    out = reg.get("list_equipment").run({})
    assert out["count"] == 24
    overdue = [e for e in out["equipment"] if (e["days_since_maintenance"] or 0) > 30]
    assert (
        len(overdue) == 10 and max(overdue, key=lambda e: e["days_since_maintenance"])["station"] == "3라인 화성(S36)"
    )
    line1 = reg.get("list_equipment").run({"line": 1})
    assert line1["count"] == 6 and all(e["line"] == 1 for e in line1["equipment"])


def test_factory_dashboard_has_equipment_board(factory_db):
    from harness.packs import check_panels
    from harness.packs.factory.dashboard import dashboard

    db, _ = factory_db
    panels = check_panels(dashboard(db_path=db))
    board = next(p for p in panels if p["id"] == "equipment")
    assert len(board["data"]["rows"]) == 24 and board["data"]["columns"][-1] == "상태"
    stats = next(p for p in panels if p["id"] == "stats")
    assert any(s["label"] == "주의 설비" for s in stats["data"])


def test_spice_engine_status(tmp_path):
    from harness.packs.spice.tools import build_tools as build_spice

    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "sim_a").mkdir()
    (runs / "sim_a" / "meta.json").write_text(
        '{"run_id": "sim_a", "title": "* t", "errors": [], "elapsed_ms": 20}', encoding="utf-8"
    )
    (runs / "sim_b").mkdir()
    (runs / "sim_b" / "meta.json").write_text(
        '{"run_id": "sim_b", "title": "* u", "errors": ["x"], "elapsed_ms": 40}', encoding="utf-8"
    )
    (runs / "sw_c").mkdir()
    (runs / "sw_c" / "meta.json").write_text(
        '{"run_id": "sw_c", "title": "* s", "rows": [], "failed": 0, "elapsed_ms": 60}', encoding="utf-8"
    )
    reg = ToolRegistry(build_spice(runs_dir=runs, library_dir=tmp_path / "lib", graph_path=tmp_path / "g.sqlite"))
    out = reg.get("engine_status").run({})
    day = out["last_24h"]
    assert out["runs_total"] == 3 and (day["runs"], day["simulations"], day["sweeps"], day["failed"]) == (3, 2, 1, 1)
    assert day["avg_elapsed_ms"] == 40 and day["last_run"]["kind"] == "sweep" and day["last_run"]["title"] == "s"
    assert out["saved_circuits"] == 0 and "ngspice" in out and "version" in out["ngspice"]
