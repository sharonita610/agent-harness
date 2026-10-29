"""소자 라이브러리: 검색·조회, parts 자동 부착, 모델 카드로 얻는 값(평가 정답과 같은 값)."""

import pytest

from harness.packs.spice.library import LIBRARY
from harness.packs.spice.netlist import parse_netlist
from harness.packs.spice.simulator import ngspice_available
from harness.packs.spice.tools import build_tools
from harness.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def reg(tmp_path_factory):
    return ToolRegistry(build_tools(runs_dir=tmp_path_factory.mktemp("runs"), index=None))


def test_search_and_get(reg):
    npn = reg.get("search_parts").run({"query": "", "kind": "npn"})
    assert {p["name"] for p in npn["parts"]} == {"2N2222", "2N3904", "BC547"}
    zener = reg.get("search_parts").run({"query": "제너"})["parts"]
    assert zener and zener[0]["name"] == "ZENER_5V1"
    part = reg.get("get_part").run({"name": "2n2222"})  # 대소문자 무시, model 이름으로도 찾는다
    assert part["card"].startswith(".model Q2N2222 NPN") and "collector base emitter" in part["element_example"]
    assert reg.get("get_part").run({"name": "Q2N3906"})["name"] == "2N3906"
    with pytest.raises(ValueError, match="라이브러리에 없는 소자"):
        reg.get("get_part").run({"name": "XYZ999"})


def test_with_parts_appends_cards_once_and_keeps_end():
    net = "* d\nI1 0 a dc 1m\nD1 a 0 D1N4148\n.op\n.end"
    out = LIBRARY.with_parts(net, ["1N4148", "1N4148"])
    assert out.count(".model D1N4148") == 1 and out.rstrip().endswith(".end")
    nl = parse_netlist(out)
    assert "D1N4148" in nl.models
    assert LIBRARY.with_parts(net, []) == net
    with pytest.raises(ValueError, match="라이브러리에 없는 소자"):
        LIBRARY.with_parts(net, ["nope"])


@pytest.mark.skipif(not ngspice_available(), reason="ngspice 미설치")
def test_library_models_simulate_to_the_evaluation_truths(reg):
    vf = reg.get("run_simulation").run(
        {"netlist": "* vf\nI1 0 a dc 1m\nD1 a 0 D1N4148\n.op", "parts": ["1N4148"], "measurements": []}
    )
    assert abs(vf["measurements"]["v(a)"] - 0.5847) < 0.005  # tasks_spice_library lib01 (.op 는 노드 이름으로 돌아온다)
    z = reg.get("run_simulation").run(
        {
            "netlist": "* zener\nV1 in 0 dc 12\nR1 in out 1k\nD1 0 out DZ5V1\n.op",
            "parts": ["ZENER_5V1"],
            "measurements": [],
        }
    )
    assert abs(z["measurements"]["v(out)"] - 5.117) < 0.03  # lib02


def test_requested_part_overrides_same_name_model_line():
    """평가에서 모델이 `.model D1N4148 D`(기본값)를 직접 적고 parts 도 준 경우: 요청한 라이브러리 카드가 이긴다."""
    nl = "* d\nI1 0 1 1m\nD1 1 0 D1N4148\n.model D1N4148 D\n.op\n.end"
    out, notes = LIBRARY.apply_parts(nl, ["1N4148"])
    assert out.count(".model D1N4148") == 1 and "IS=" in out.upper() and "대체" in notes[0]
    assert out.rstrip().endswith(".end")
    out2, notes2 = LIBRARY.apply_parts("* d\nD1 1 0 D1N4148\n.op", ["1N4148"])
    assert notes2 == ["1N4148: 라이브러리 카드 추가"] and ".model D1N4148" in out2
    assert LIBRARY.apply_parts("* d\n.op", None) == ("* d\n.op", [])


def test_part_name_in_element_line_and_wrong_letter_are_fixed():
    """평가에서 본 습관: 소자 줄에 부품 이름(1N4148)을 쓰거나 제너를 Z1 로 시작하는 것. 둘 다 고쳐지고 메모에 남는다."""
    out, notes = LIBRARY.apply_parts("* d\nI1 0 1 1m\nD1 1 0 1N4148\n.op", ["1N4148"])
    assert "D1 1 0 D1N4148" in out and any("부품 이름 1N4148 을 model 이름 D1N4148" in n for n in notes)
    out2, notes2 = LIBRARY.apply_parts("* z\nV1 in 0 12\nR1 in out 1k\nZ1 0 out ZENER_5V1\n.op", ["ZENER_5V1"])
    assert "DZ1 0 out DZ5V1" in out2 and any("'DZ1'" in n for n in notes2)
    out3, notes3 = LIBRARY.apply_parts("* ok\nD1 1 0 D1N4148\n.op", ["1N4148"])  # 이미 맞게 쓴 줄은 그대로
    assert "D1 1 0 D1N4148" in out3 and notes3 == ["1N4148: 라이브러리 카드 추가"]


@pytest.mark.skipif(not ngspice_available(), reason="ngspice 없음")
def test_fixed_zener_line_simulates_to_truth(tmp_path):
    from harness.packs.spice.simulator import simulate

    nl, _ = LIBRARY.apply_parts("* z\nV1 in 0 12\nR1 in out 1k\nZ1 0 out ZENER_5V1\n.op", ["ZENER_5V1"])
    r = simulate(nl, [], [], runs_dir=tmp_path, timeout_s=20)
    assert abs(r["measurements"]["v(out)"] - 5.117) < 0.03
