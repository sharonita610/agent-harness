"""실험 추적 그래프: 기록(회로 중복 제거·변형 연결) → 질의 → 비교 → 계보 → 그림. ngspice 없이 가짜 결과로 검증한다."""

from harness.packs.spice.graph import ExperimentGraph, render_graph_svg
from harness.packs.spice.netlist import parse_netlist

RC = "* RC\nV1 in 0 dc 0 ac 1\nR1 in out {R}\nC1 out 0 100n\n.ac dec 20 10 1meg"


def _res(rid, fc):
    return {
        "run_id": rid,
        "analysis": ".ac dec 20 10 1meg",
        "analysis_kind": "ac",
        "points": 101,
        "measurements": {"fc": fc},
        "warnings": [],
        "errors": [],
        "elapsed_ms": 10,
    }


def test_record_links_variants_and_dedupes_circuits(tmp_path):
    g = ExperimentGraph(tmp_path / "g.sqlite")
    a = g.record_simulation(parse_netlist(RC.replace("{R}", "1k")), _res("sim_a", 1588.0), question="첫 실행")
    b = g.record_simulation(parse_netlist(RC.replace("{R}", "2.2k")), _res("sim_b", 721.7))
    c = g.record_simulation(parse_netlist(RC.replace("{R}", "1k")), _res("sim_c", 1588.0))
    assert a["variants"] == [] and b["variants"][0][1] == [
        {"component": "R1", "from": "2.2k", "to": "1k"}
    ]  # 새 회로(2.2k) 에서 기존 변형(1k) 으로
    assert a["circuit_id"] == c["circuit_id"] != b["circuit_id"]
    st = g.stats()
    assert st["nodes"]["circuit"] == 2 and st["nodes"]["run"] == 3 and st["edges"]["variant_of"] == 2


def test_query_compare_family(tmp_path):
    g = ExperimentGraph(tmp_path / "g.sqlite")
    g.record_simulation(parse_netlist(RC.replace("{R}", "1k")), _res("sim_a", 1588.0))
    g.record_simulation(parse_netlist(RC.replace("{R}", "2.2k")), _res("sim_b", 721.7))
    g.record_simulation(
        parse_netlist("* other\nV1 a 0 dc 5\nR1 a 0 1k\n.op"),
        {
            "run_id": "sim_x",
            "analysis": ".op",
            "analysis_kind": "op",
            "points": 1,
            "measurements": {"a": 5.0},
            "warnings": [],
            "errors": [],
            "elapsed_ms": 1,
        },
    )
    hits = g.query_runs(component="R1", value=2200)
    assert (
        [h["run_id"] for h in hits] == ["sim_b"]
        and hits[0]["components"]["R1"] == "2.2k"
        and hits[0]["measurements"]["fc"] == 721.7
    )
    assert [h["run_id"] for h in g.query_runs(measurement="fc", value_min=1000)] == ["sim_a"]
    assert len(g.query_runs(title_contains="RC")) == 2 and len(g.query_runs(component="R1", value=1000)) == 2
    cmp = g.compare_runs("sim_a", "sim_b")
    assert (
        cmp["same_topology"]
        and not cmp["same_circuit"]
        and cmp["component_diffs"] == [{"component": "R1", "a": "1k", "b": "2.2k"}]
    )
    assert abs(cmp["measurements"]["fc"]["ratio"] - 0.4545) < 0.001
    fam = g.family("sim_a")
    assert len(fam["circuits"]) == 2 and fam["circuits"][1]["diff_from_base"] == [
        {"component": "R1", "from": "1k", "to": "2.2k"}
    ]
    assert fam["circuits"][0]["runs"][0]["run_id"] == "sim_a"


def test_sweep_points_are_queryable_and_svg_renders(tmp_path):
    g = ExperimentGraph(tmp_path / "g.sqlite")
    g.record_simulation(parse_netlist(RC.replace("{R}", "1k")), _res("sim_a", 1588.0))
    g.record_sweep(
        parse_netlist(RC.replace("{R}", "1k")),
        {
            "run_id": "swp_1",
            "kind": "sweep",
            "component": "R1",
            "measurement": "fc",
            "analysis": ".ac dec 20 10 1meg",
            "analysis_kind": "ac",
            "points": 2,
            "failed": 0,
            "elapsed_ms": 5,
            "rows": [{"value": 470.0, "fc": 3386.0}, {"value": 4700.0, "fc": 338.6}],
        },
    )
    hit = g.query_runs(component="R1", value=4700)
    assert hit and hit[0]["kind"] == "sweep_point" and hit[0]["measurements"]["fc"] == 338.6
    svg = render_graph_svg(g)
    assert svg.startswith("<svg") and "sim_a" in svg and "swp_1" in svg and "fc = 1588" in svg


def test_clear_keeps_other_connections_valid(tmp_path):
    """시드가 파일을 지우면 도구가 연 연결이 'readonly database' 로 죽는다. 표만 비우면 두 연결 모두 계속 쓴다."""
    path = tmp_path / "g.sqlite"
    tool_side = ExperimentGraph(path)
    tool_side.record_simulation(parse_netlist(RC.replace("{R}", "1k")), _res("sim_a", 1588.0))
    seeder = ExperimentGraph(path)
    seeder.clear()
    assert tool_side.stats()["nodes"] == {} and seeder.stats()["nodes"] == {}
    tool_side.record_simulation(parse_netlist(RC.replace("{R}", "2.2k")), _res("sim_b", 721.7))  # 여전히 쓸 수 있다
    assert seeder.query_runs(component="R1", value=2200)[0]["run_id"] == "sim_b"
