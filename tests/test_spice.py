"""spice 팩: 넷리스트 가드·파서, 시뮬레이터(ngspice 있을 때), 스윕, 그래프, 도구."""
import math
import os

import pytest

from harness.packs.spice.netlist import NetlistError, format_si, parse_netlist, parse_si, referenced_vectors, set_component_value
from harness.packs.spice.plot import Marker, figure_from_columns, render_svg
from harness.packs.spice.simulator import estimate_points, ngspice_available, normalize_measurement, parse_output, sweep_values
from harness.packs.spice.tools import CIRCUITS_DIR, build_tools
from harness.tools.registry import ToolRegistry

RC = "* RC\nV1 in 0 dc 0 ac 1\nR1 in out 1k\nC1 out 0 100n\n.ac dec 20 10 1meg"
needs_ngspice = pytest.mark.skipif(not ngspice_available(), reason="ngspice 미설치")


# ---------------------------------------------------------------- 파서·가드 (ngspice 없이)
def test_parse_si_and_format():
    assert parse_si("1k") == 1000 and parse_si("1meg") == 1e6 and parse_si("1M") == 1e-3
    assert math.isclose(parse_si("100nF"), 1e-7) and parse_si("10mH") == 0.01 and parse_si("12") == 12
    assert format_si(1591.5, "Hz").startswith("1.59") and format_si(1591.5, "Hz").endswith("kHz") and format_si(1e-4, "s") == "100 µs"


def test_parse_netlist_summary_and_title_fix():
    nl = parse_netlist(RC)
    assert nl.analysis_kind == "ac" and nl.nodes == ["in", "0", "out"] and [c.name for c in nl.components] == ["V1", "R1", "C1"]
    fixed = parse_netlist("R1 in out 1k\nC1 out 0 100n\nV1 in 0 ac 1\n.op")
    assert fixed.title == "* circuit" and len(fixed.components) == 3 and any("제목" in w for w in fixed.warnings)
    titled = parse_netlist("RC low-pass filter\nR1 in out 1k\nC1 out 0 100n\nV1 in 0 ac 1\n.op")
    assert titled.title == "* RC low-pass filter" and len(titled.components) == 3


@pytest.mark.parametrize("bad", [".include evil.cir\nR1 a 0 1k", "* t\nR1 a 0 1k\n.control\nshell ls\n.endc", "* t\nR1 a 0 1k\n.lib x.lib",
                                 "* t\nR1 a 0 1k\n.print v(a)", "* t\nR1 a 0 1k\n.save v(a)", "", "* only title"])
def test_guard_blocks_dangerous_or_empty(bad):
    with pytest.raises(NetlistError):
        parse_netlist(bad)


def test_escaped_newlines_are_unescaped():
    nl = parse_netlist("* RC\\nV1 in 0 dc 0 ac 1\\nR1 in out 1k\\nC1 out 0 100n\\n.ac dec 20 10 1meg\\n.end")
    assert [c.name for c in nl.components] == ["V1", "R1", "C1"] and nl.analysis_kind == "ac"


def test_inline_measure_is_absorbed():
    nl = parse_netlist(RC + "\n.measure ac fc WHEN vdb(out)=-3\n.end")
    assert nl.inline_measurements == ["ac fc WHEN vdb(out)=-3"] and not any(ln.startswith(".meas") for ln in nl.lines)
    assert any(".measure" in w for w in nl.warnings)


def test_warnings_for_common_mistakes():
    nl = parse_netlist("* t\nR1 a b 1M\nC1 a 0 1u\nV1 b 0 dc 1\n.op")
    assert any("밀리" in w for w in nl.warnings)
    nl = parse_netlist("* t\nR1 a b 1k\nV1 a 0 dc 1\n.op")
    assert any("하나만 연결" in w for w in nl.warnings)
    nl = parse_netlist("* t\nR1 a b 1k\nV1 a b dc 1\n.op")
    assert any("접지" in w for w in nl.warnings)


def test_set_component_value_and_refs():
    out = set_component_value(RC, "R1", 2200)
    assert "R1 in out 2200" in out
    assert "V1 in 0 dc 5" in set_component_value("* t\nV1 in 0 dc 1\nR1 in 0 1k\n.op", "V1", 5)
    with pytest.raises(NetlistError):
        set_component_value(RC, "R9", 1)
    assert referenced_vectors("WHEN vdb(out)=-3") == [("vdb", "out")]
    assert referenced_vectors("FIND v(a,b) AT=1") == [("v", "a"), ("v", "b")]


def test_measurement_normalization_and_injection():
    assert normalize_measurement("fc WHEN vdb(out)=-3", "ac")[1] == "meas ac fc WHEN vdb(out)=-3"
    name, line, warn = normalize_measurement("meas tran fc WHEN vdb(out)=-3", "ac")
    assert line.startswith("meas ac fc") and warn
    for bad in ["x WHEN vdb(out)=-3; shell ls", "x WHEN vdb(out)=-3\nquit", "1x MAX v(out)", "x SHELL ls", "x"]:
        with pytest.raises(NetlistError):
            normalize_measurement(bad, "ac")


def test_estimate_points_guard():
    assert estimate_points(".tran 1u 1m") == 1000 and estimate_points(".ac dec 20 10 1meg") == 100 and estimate_points(".dc V1 0 12 1") == 13
    assert estimate_points(".tran 1p 10") > 2_000_000


def test_parse_output_formats():
    out = "Error: no such vector as v(x).\nNo. of Data Rows : 101\n    out : voltage, complex, 101 long\nfc = 1.58778e+03\nv1#branch = -3e-3\n meas ac g failed!\n"
    p = parse_output(out)
    assert p["results"] == {"fc": 1587.78, "v1#branch": -0.003} and p["points"] == 101 and p["vectors"][0]["name"] == "out"
    assert len(p["errors"]) == 2


def test_sweep_values():
    assert sweep_values(None, None, None, False, [1, 2]) == [1.0, 2.0]
    log = sweep_values(100, 10000, 3, True, None)
    assert [round(v) for v in log] == [100, 1000, 10000]
    assert sweep_values(0, 10, 3, False, None) == [0, 5, 10]
    with pytest.raises(NetlistError):
        sweep_values(None, None, None, True, None)


def test_plot_svg_without_ngspice():
    cols = ["frequency", "vdb(out)", "vp(out)"]
    rows = [[10.0 ** (i / 4), -3 * i / 10, -i] for i in range(21)]
    fig = figure_from_columns(cols, rows, ["vdb(out)"], title="t", markers=[Marker("fc", x=100), Marker("-3 dB", y=-3)])
    svg = render_svg(fig)
    assert svg.startswith("<svg") and fig.x_log and "<path" in svg and "fc" in svg and "1 kHz" in svg
    with pytest.raises(ValueError):
        figure_from_columns(cols, rows, ["nope"])


# ---------------------------------------------------------------- 실제 ngspice
@pytest.fixture
def reg(tmp_path):
    return ToolRegistry(build_tools(runs_dir=tmp_path / "runs", library_dir=tmp_path / "lib", index=None))


def test_tool_registry_shape(reg):
    names = reg.names()
    assert len(names) == 11 and "run_simulation" in names and reg.get("save_circuit").tier == "write" and reg.get("delete_circuit").tier == "destructive"
    circuits = reg.get("list_circuits").run({})["circuits"]
    assert {c["name"] for c in circuits} >= {"rc_lowpass", "rc_highpass", "rlc_bandpass", "voltage_divider", "rc_step", "cmos_inverter"}
    assert reg.get("check_netlist").run({"netlist": RC})["ok"]
    assert reg.get("search_manual").run({"query": "차단 주파수 공식", "k": 2})[0]["doc"]


@needs_ngspice
def test_rc_lowpass_cutoff_matches_formula(reg):
    r = reg.get("run_simulation").run({"netlist": RC, "measurements": ["fc WHEN vdb(out)=-3", "p FIND vp(out) AT=1591.5"]})
    fc = r["measurements"]["fc"]
    assert abs(fc - 1 / (2 * math.pi * 1e3 * 100e-9)) / 1591.5 < 0.01
    assert abs(r["measurements"]["p"] + 45) < 1  # 위상은 도 단위
    assert r["points"] == 101 and r["saved_vectors"] == ["vdb(in)", "vdb(out)", "vp(out)"] and "kHz" in r["measurements_readable"]["fc"]
    w = reg.get("get_waveform").run({"run_id": r["run_id"], "vectors": ["vdb(out)"], "max_points": 5})
    assert w["columns"] == ["frequency", "vdb(out)"] and w["returned"] == 5 and w["total_points"] == 101
    p = reg.get("plot_waveform").run({"run_id": r["run_id"], "mark_x": fc, "mark_x_label": "fc", "mark_y": -3})
    assert p["url"].endswith("/plot.svg") and os.path.exists(p["svg_path"]) and p["series"] == ["vdb(in)", "vdb(out)"]  # 교류 기본은 vdb 열만


@needs_ngspice
def test_samples_all_run_and_match_physics():
    from harness.packs.spice.simulator import simulate
    tau = simulate((CIRCUITS_DIR / "rc_step.cir").read_text(), ["tau WHEN v(out)=0.632", "trise TRIG v(out) VAL=0.1 RISE=1 TARG v(out) VAL=0.9 RISE=1"])["measurements"]
    assert abs(tau["tau"] - 1e-4) / 1e-4 < 0.01 and abs(tau["trise"] - math.log(9) * 1e-4) / 2.197e-4 < 0.01
    op = simulate((CIRCUITS_DIR / "voltage_divider.cir").read_text())["measurements"]
    assert op["out"] == 3.0 and abs(op["v1#branch"] + 0.003) < 1e-9
    rlc = simulate((CIRCUITS_DIR / "rlc_bandpass.cir").read_text(), ["f0 MAX_AT vdb(out)"])["measurements"]
    assert abs(rlc["f0"] - 5032.9) / 5032.9 < 0.02
    inv = simulate((CIRCUITS_DIR / "cmos_inverter.cir").read_text(), ["vm WHEN v(out)=1.65"])["measurements"]
    assert 1.4 < inv["vm"] < 1.9


@needs_ngspice
def test_failed_measurement_and_errors(reg):
    r = reg.get("run_simulation").run({"netlist": RC, "measurements": ["fc WHEN vdb(out)=-100"]})
    assert r["measurements"]["fc"] is None and any("값이 나오지 않은" in w for w in r["warnings"])
    with pytest.raises(NetlistError):
        reg.get("run_simulation").run({"netlist": RC, "measurements": ["fc WHEN vdb(nothere)=-3"]})
    with pytest.raises(NetlistError):
        reg.get("run_simulation").run({"netlist": "* t\nV1 a 0 dc 1\nR1 a 0 1k", "measurements": []})
    from harness.packs.spice.simulator import SimulationError
    with pytest.raises(SimulationError):
        reg.get("run_simulation").run({"netlist": "* sing\nV1 a 0 dc 1\nV2 a 0 dc 2\n.op", "measurements": []})


@needs_ngspice
def test_sweep_and_plot(reg):
    s = reg.get("run_sweep").run({"netlist": RC, "component": "R1", "measurement": "fc WHEN vdb(out)=-3", "start": 100, "stop": 10000, "points": 5, "log": True})
    assert s["kind"] == "sweep" and s["points"] == 5 and s["failed"] == 0
    fcs = [r["fc"] for r in s["rows"]]
    assert fcs == sorted(fcs, reverse=True) and abs(fcs[2] - 1591.5) / 1591.5 < 0.01
    p = reg.get("plot_waveform").run({"run_id": s["run_id"], "title": "R1 스윕"})
    assert p["series"] == ["fc"] and os.path.exists(p["svg_path"])
    with pytest.raises(Exception):
        reg.get("run_sweep").run({"netlist": RC, "component": "R1", "measurement": "fc WHEN vdb(out)=-3", "values": [1] * 61})


def test_save_and_delete_circuit(reg, tmp_path):
    out = reg.get("save_circuit").run({"name": "my_rc", "netlist": RC, "description": "테스트"})
    assert out["saved"] == "my_rc" and (tmp_path / "lib" / "my_rc.cir").exists()
    saved = [c for c in reg.get("list_circuits").run({})["circuits"] if c["source"] == "saved"]
    assert saved and saved[0]["description"] == "테스트"
    with pytest.raises(ValueError):
        reg.get("save_circuit").run({"name": "rc_lowpass", "netlist": RC})
    with pytest.raises(ValueError):
        reg.get("save_circuit").run({"name": "my_rc", "netlist": RC})
    with pytest.raises(ValueError):
        reg.get("delete_circuit").run({"name": "rc_lowpass"})
    assert reg.get("delete_circuit").run({"name": "my_rc"}) == {"deleted": "my_rc"}


@needs_ngspice
def test_op_ignores_measurements_with_warning(reg):
    r = reg.get("run_simulation").run({"netlist": "* div\nV1 in 0 dc 12\nR1 in out 3k\nR2 out 0 1k\n.op", "measurements": ["v(out)", "i(V1)"]})
    assert r["measurements"]["out"] == 3.0 and any("무시" in w for w in r["warnings"])


def test_prompt_v2_differs():
    from harness.packs.spice.tools import SYSTEM_PROMPTS
    assert SYSTEM_PROMPTS["v1"] != SYSTEM_PROMPTS["v2"] and "반드시 search_manual" in SYSTEM_PROMPTS["v2"]
