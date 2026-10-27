"""실험 추적 그래프 시드: 그래프 파일을 비우고 기준 회로 몇 개를 실제로 시뮬레이션해 기록한다.

평가 태스크(h01~)가 "기록에서 R1=2.2kΩ 실행의 fc" 같은 질문을 하려면 모든 머신에서 같은 기록에서 출발해야 한다.
주의: 기존 기록을 지운다. 실제 작업 기록을 보존하려면 HARNESS_SPICE_GRAPH 를 다른 파일로 두고 시드한다.
"""
from __future__ import annotations

from pathlib import Path

from .graph import GRAPH_PATH, ExperimentGraph
from .netlist import parse_netlist
from .simulator import RUNS_DIR, simulate

RC = "* RC 저역통과 기준 회로 (R1={R}, C1=100n)\nV1 in 0 dc 0 ac 1\nR1 in out {R}\nC1 out 0 100n\n.ac dec 20 10 1meg"
STEP = "* RC 계단 응답 기준 회로 (R1=1k, C1=100n)\nV1 in 0 PULSE(0 1 0 1n 1n 10m 20m)\nR1 in out 1k\nC1 out 0 100n\n.tran 1u 1m"


def seed_history(graph_path: Path = GRAPH_PATH, runs_dir: Path = RUNS_DIR) -> dict:
    graph_path = Path(graph_path)
    if graph_path.exists():
        graph_path.unlink()
    g = ExperimentGraph(graph_path)
    recorded = []
    for R in ("470", "1k", "2.2k", "4.7k"):
        text = RC.replace("{R}", R)
        r = simulate(text, ["fc WHEN vdb(out)=-3"], runs_dir=runs_dir)
        g.record_simulation(parse_netlist(text), r, question=f"시드: RC 저역통과 R1={R}")
        recorded.append((R, round(r["measurements"]["fc"], 1)))
    r = simulate(STEP, ["tau WHEN v(out)=0.632"], runs_dir=runs_dir)
    g.record_simulation(parse_netlist(STEP), r, question="시드: RC 계단 응답")
    return {"graph": str(graph_path), "seeded_runs": len(recorded) + 1, "fc_by_R1": recorded, **g.stats()}
