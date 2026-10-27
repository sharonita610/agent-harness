"""공정 NG 분석 팩: 시드 결정성, 심어 둔 이상 탐지(급증·원인·야간 패턴·로트), 조치 효과, 쓰기 도구, 권한."""
import pytest

from harness.packs.factory.seed import build
from harness.packs.factory.tools import build_tools
from harness.tools.permissions import Decision, Policy
from harness.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def reg(factory_db, tmp_path_factory):
    db, _ = factory_db
    return ToolRegistry(build_tools(db_path=db, runs_dir=tmp_path_factory.mktemp("runs"), index=None))


def test_seed_is_deterministic(tmp_path):
    a, b = build(tmp_path / "a.db"), build(tmp_path / "b.db")
    assert a["sha256_16"] == b["sha256_16"] == "0fbe098605883ba6" and a["counts"]["ng_events"] > 20000 and a["counts"]["stations"] == 24


def test_tool_shape_and_policy(reg):
    assert len(reg.names()) == 16
    pol = Policy()
    assert pol.decide(reg.get("confirm_label")) == Decision.REQUIRE_APPROVAL and pol.decide(reg.get("create_action")) == Decision.REQUIRE_APPROVAL
    assert pol.decide(reg.get("delete_ng_events")) == Decision.DENY and pol.decide(reg.get("detect_spikes")) == Decision.ALLOW


def test_spike_detection_finds_planted_anomaly_first(reg):
    out = reg.get("detect_spikes").run({})
    top = out["spikes"][0]
    assert top["station_id"] == 23 and top["start"] == "2026-10-18 09:00:00" and top["end"] == "2026-10-18 14:00:00" and top["multiplier"] > 3
    assert top["top_defect"] == "정렬 불량" and top["shift"] == "주간"
    only = reg.get("detect_spikes").run({"line": 2, "station": "권취", "from_ts": "2026-10-18", "to_ts": "2026-10-19"})
    assert only["count"] == 1


def test_correlation_points_at_heater_temperature(reg):
    c = reg.get("correlate_params").run({"line": 2, "station": "권취", "from_ts": "2026-10-18 09:00", "to_ts": "2026-10-18 14:00"})
    assert c["param_candidates"][0]["param"] == "temp_c" and c["param_candidates"][0]["z"] > 5 and c["param_candidates"][0]["diff"] > 6
    assert c["maintenance_near_window"] and c["maintenance_near_window"][0]["kind"] == "히터 교체"
    assert any("PE-007" in h for h in c["hints"])


def test_night_pattern_and_lot_defect(reg):
    by_shift = {r["k"]: r["ng_rate_pct"] for r in reg.get("ng_summary").run({"station": "45", "group_by": "shift"})["rows"]}
    assert by_shift["야간"] > 1.3 * by_shift["주간"]
    ev = reg.get("list_ng_events").run({"material_batch": "M-7781", "limit": 5})
    assert ev["total_matching"] > 200 and all(e["material_batch"] == "M-7781" for e in ev["events"])
    d = reg.get("ng_summary").run({"line": 1, "from_ts": "2026-10-22 08:00", "to_ts": "2026-10-22 16:00", "group_by": "defect"})["by_defect"]
    assert d[0]["defect"] == "정렬 불량"


def test_before_after_and_plot(reg):
    r = reg.get("compare_before_after").run({"action_id": 1})
    assert r["change_pct"] <= -30 and r["verdict"].startswith("효과 있음")
    p = reg.get("plot_ng_rate").run({"station": "23", "from_ts": "2026-10-17", "to_ts": "2026-10-20"})
    assert p["url"].endswith("/plot.svg") and p["max_pct"] > 3 * p["baseline_pct"]


def test_station_resolution_errors(reg):
    with pytest.raises(ValueError):
        reg.get("detect_spikes").run({"station": "99"})
    with pytest.raises(ValueError):
        reg.get("correlate_params").run({"station": "권취", "from_ts": "2026-10-18", "to_ts": "2026-10-19"})  # 라인 없이 공정 이름 → 4개
    assert reg.get("run_sql").run({"sql": "SELECT COUNT(*) AS n FROM ng_events"})["rows"][0]["n"] > 20000
    with pytest.raises(ValueError):
        reg.get("run_sql").run({"sql": "DELETE FROM ng_events"})


def test_write_tools_change_data(reg):
    ev = reg.get("list_ng_events").run({"status": "suggested", "defect": "이물", "limit": 1})["events"][0]
    out = reg.get("confirm_label").run({"event_id": ev["id"], "label": "이물", "note": "테스트"})
    assert out["after"]["confirmed_label"] == "이물"
    with pytest.raises(ValueError):
        reg.get("confirm_label").run({"event_id": ev["id"], "label": "없는유형"})
    act = reg.get("create_action").run({"line": 2, "station": "권취", "title": "히터 설정값 재확인", "owner": "김엔지니어"})
    assert act["status"] == "open" and "권취" in act["station"]
    with pytest.raises(ValueError):
        reg.get("create_action").run({"line": 2, "station": "권취", "title": "x", "owner": "아무개"})
    rule = reg.get("set_alert_rule").run({"station": "23", "threshold_multiplier": 2.5, "window_hours": 2})
    assert rule["threshold_multiplier"] == 2.5


def test_pack_loads_and_eval_truths_resolve(factory_db):
    import sqlite3

    import yaml

    from harness.evals.runner import resolve
    from harness.packs import load_pack
    pk = load_pack("factory")
    assert pk.db_path and pk.seed is not None and "factory" in pk.files
    tasks = yaml.safe_load(pk.tasks_path.read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{factory_db[0]}?mode=ro", uri=True)
    truths = {t["id"]: resolve(t, conn)[1] for t in tasks}
    assert truths["q01"] == [532] and truths["q03"] == [303] and truths["q02"] == ["2라인"] and len(tasks) == 17
