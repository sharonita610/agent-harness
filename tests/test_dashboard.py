"""팩 대시보드("데이터" 탭): 패널 규약을 지키고, 심어 둔 이상이 표에 보이고, 사용자 범위가 적용되는지."""
from pathlib import Path

from fastapi.testclient import TestClient

from harness.packs import PANEL_KINDS, check_panels
from harness.packs.enterprise.dashboard import dashboard as enterprise_dashboard
from harness.packs.enterprise.scope import Scope
from harness.packs.factory.dashboard import dashboard as factory_dashboard
from harness.packs.spice.dashboard import dashboard as spice_dashboard
from harness.packs.spice.tools import CIRCUITS_DIR
from harness.packs.supervisor.dashboard import dashboard as supervisor_dashboard
from harness.service.app import create_app


def _by_id(panels):
    return {p["id"]: p for p in panels}


def test_enterprise_dashboard_shapes_and_scope(seeded_db):
    db, _ = seeded_db
    panels = check_panels(enterprise_dashboard(db_path=db))
    p = _by_id(panels)
    assert {"stats", "status", "monthly", "reasons", "shortage", "recent"} <= set(p)
    total = next(s for s in p["stats"]["data"] if s["label"] == "전체 주문")["value"]
    assert total > 0 and all(pn["kind"] in PANEL_KINDS for pn in panels)
    assert p["monthly"]["data"]["x"] and len(p["monthly"]["data"]["series"]) == 2
    scoped = _by_id(enterprise_dashboard(scope=Scope.parse("region=부산"), db_path=db))
    scoped_total = next(s for s in scoped["stats"]["data"] if s["label"] == "전체 주문")["value"]
    assert 0 < scoped_total < total                          # 범위가 걸리면 비서가 보는 것과 같은 부분집합
    assert "부산" in scoped["stats"]["title"]


def test_factory_dashboard_shows_planted_anomaly(factory_db):
    db, _ = factory_db
    p = _by_id(check_panels(factory_dashboard(db_path=db)))
    assert p["heat"]["data"]["rows"] == ["1라인", "2라인", "3라인", "4라인"] and len(p["heat"]["data"]["cols"]) == 6
    assert len(p["heat"]["data"]["values"]) == 4 and all(len(r) == 6 for r in p["heat"]["data"]["values"])
    assert len(p["daily"]["data"]["x"]) == 14 and len(p["daily"]["data"]["series"]) == 4
    spikes = p["spikes"]["data"]["rows"]
    assert spikes and spikes[0]["스테이션"] == "2라인 권취"      # 이상 A 가 배수 1위
    assert spikes[0]["배수"] >= 3
    assert p["tickets"]["kind"] == "table" and p["defects"]["data"]["x"]


def test_spice_dashboard_with_empty_runs(tmp_path: Path):
    runs = tmp_path / "runs"; runs.mkdir()
    (runs / "sim_x").mkdir()
    (runs / "sim_x" / "meta.json").write_text('{"run_id": "sim_x", "analysis": ".ac dec 20 10 1meg", "analysis_kind": "ac", "points": 101, "measurements": {"fc": 1588.0}, "elapsed_ms": 40, "title": "* rc"}', encoding="utf-8")
    p = _by_id(check_panels(spice_dashboard(runs_dir=runs, circuits_dir=CIRCUITS_DIR, library_dir=tmp_path / "lib", graph_path=tmp_path / "g.sqlite")))
    assert len(p["circuits"]["data"]["rows"]) >= 3
    assert p["runs"]["data"]["rows"][0]["측정값"].startswith("fc=1.588")
    assert p["kinds"]["data"]["x"] == ["ac"]
    assert "graph" not in p                                   # 실행 그래프가 비면 그림 패널 없음


def test_supervisor_dashboard_reports_each_agent(seeded_db, tmp_path: Path):
    client = TestClient(create_app(db_path=str(tmp_path / "svc.sqlite"), pack="enterprise"))
    p = _by_id(check_panels(supervisor_dashboard(agents=["http://127.0.0.1:8786", "http://127.0.0.1:9"], http=client)))
    rows = p["agents"]["data"]["rows"]
    assert rows[0]["연결"] == "연결됨" and rows[0]["팩"] == "enterprise" and rows[0]["도구"] == 10
    assert len(rows) == 2                                     # TestClient 는 호스트를 가리지 않아 둘 다 같은 앱으로 간다
    assert p["stats"]["data"][0]["value"] == "2/2"


def test_dashboard_endpoint_and_health_flag(seeded_db, tmp_path: Path):
    db, _ = seeded_db
    client = TestClient(create_app(db_path=str(tmp_path / "svc.sqlite"), pack="enterprise"))
    assert client.get("/health").json()["pack"]["dashboard"] is True
    panels = client.get("/dashboard").json()
    assert isinstance(panels, list) and panels[0]["kind"] == "stats"
