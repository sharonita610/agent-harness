"""팩 인터페이스: 플랫폼(서비스·평가·MCP)이 팩을 바꿔 끼울 수 있는지."""
import json

from fastapi.testclient import TestClient

from harness.evals.runner import resolve, safe_eval, score, within_tolerance
from harness.packs import PACK_NAMES, load_pack
from harness.service.app import create_app


def test_load_packs_have_uniform_interface():
    for name in PACK_NAMES:
        pk = load_pack(name)
        assert pk.name == name and pk.title and pk.system_prompt and "v1" in pk.system_prompts
        assert pk.tasks_path.name.endswith(".yaml") and pk.docs_dir.exists() and callable(pk.build_tools)
        assert pk.scope_note(pk.parse_scope(None)) == ""
    assert load_pack("spice").parse_scope("region=부산") is None  # 회로 팩엔 데이터 범위 개념이 없다


def test_service_with_spice_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_SPICE_RUNS", str(tmp_path / "runs"))
    app = create_app(db_path=tmp_path / "svc.db", pack="spice")
    with TestClient(app) as c:
        h = c.get("/health").json()
        assert h["pack"]["name"] == "spice" and h["tools"] == 11 and "run_simulation" in h["pack"]["tool_labels"]
        tools = c.get("/tools").json()
        assert {t["name"] for t in tools} >= {"run_simulation", "run_sweep", "plot_waveform"} and tools[0]["label"]
        assert c.get("/me").json()["pack"] == "spice"
        assert c.get("/files/spice/nothing.svg").status_code == 404  # 마운트는 됐고 파일만 없다


def test_service_default_pack_is_enterprise(seeded_db, tmp_path):
    from harness.packs.enterprise.tools import SYSTEM_PROMPT, build_tools
    from harness.tools.registry import ToolRegistry
    app = create_app(db_path=tmp_path / "svc.db", registry=ToolRegistry(build_tools(seeded_db[0])), system_prompt=SYSTEM_PROMPT)
    with TestClient(app) as c:
        assert c.get("/health").json()["pack"]["name"] == "enterprise"


def test_mcp_server_builds_for_both_packs():
    from harness.mcp_server.enterprise_server import build_server
    from harness.packs.spice.tools import build_tools
    from harness.tools.registry import ToolRegistry
    s = build_server(registry=ToolRegistry(build_tools(index=None)), pack="spice")
    assert s.name == "spice-sim"


def test_formula_truth_and_tolerance_scoring():
    from harness.agent.types import AgentResult
    task = {"id": "s01", "type": "sim", "question": "RC 차단 주파수?", "truth_expr": "1/(2*pi*1e3*100e-9)", "tolerance_pct": 2,
            "expect_tools_any": ["run_simulation"]}
    q, truth = resolve(task, None)
    assert abs(truth[0] - 1591.55) < 0.01
    ok = AgentResult(answer="시뮬레이션 결과 차단 주파수는 약 1.588 kHz 입니다.", steps=3, stopped_reason="final",
                     tool_calls=[{"name": "run_simulation", "status": "ok"}])
    bad = AgentResult(answer="약 1.3 kHz 입니다.", steps=3, stopped_reason="final", tool_calls=[{"name": "run_simulation", "status": "ok"}])
    assert score(task, ok, truth)["pass"] and not score(task, bad, truth)["pass"]
    assert within_tolerance(1e-4, "시정수는 100 µs", 1) and within_tolerance(2.197e-4, "상승 시간 219.7µs", 1)
    assert abs(safe_eval("sqrt(1e-3/1e-6)/10") - 10 ** 1.5 / 10) < 1e-12


def test_extract_numbers_with_space_thousands():
    from harness.evals.runner import extract_numbers
    assert 15877.8 in extract_numbers("| 100 | 15 877.8 Hz | 15 915.5 Hz |") and 1587.78 in extract_numbers("1 587.78 Hz")
    assert within_tolerance(1591.5, "| 1 000 | 1 587.78 Hz |", 2)
