"""긴 도구의 진행률: 수신자 없으면 무시, 루프 안에서는 progress 이벤트로 남고(0.4초 묶음, 마지막 점은 항상), 스윕이 점마다 보고한다."""

import pytest
from pydantic import BaseModel

from harness.agent.loop import Agent
from harness.agent.types import LLMResponse, ToolCall
from harness.packs.spice.simulator import ngspice_available, sweep
from harness.tools.progress import progress_scope, report
from harness.tools.registry import Tier, Tool, ToolRegistry


def test_report_is_noop_without_scope_and_calls_scope_callback():
    report(1, 2, "아무도 안 받음")  # 예외 없음
    got = []
    with progress_scope(lambda d, t, l: got.append((d, t, l))):
        report(1, 3, "a")
        report(3, 3, "b")
    assert got == [(1, 3, "a"), (3, 3, "b")]
    report(9, 9, "x")  # 범위 밖
    assert len(got) == 2


class NoArgs(BaseModel):
    pass


class _Script:
    def __init__(self):
        self.n = 0

    def complete(self, messages, tools=None):
        self.n += 1
        if self.n == 1:
            return LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="c1", name="slow", arguments={})],
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        return LLMResponse(text="끝", tool_calls=[], usage={"prompt_tokens": 1, "completion_tokens": 1})


def test_loop_emits_progress_events_with_last_point_guaranteed():
    def slow(_: NoArgs):
        for i in range(5):
            report(i + 1, 5, f"점 {i + 1}")  # 0.4초 안에 다 끝나므로 첫 점과 마지막 점만 남아야 한다
        return {"ok": True}

    reg = ToolRegistry([Tool("slow", "느린 도구", NoArgs, slow, Tier.READ)])
    res = Agent(_Script(), reg, "t", max_steps=3).run("가")
    prog = [e for e in res.events if e.kind == "progress"]
    assert [p.data["done"] for p in prog] == [1, 5]
    assert prog[-1].data == {"name": "slow", "done": 5, "total": 5, "label": "점 5"}


@pytest.mark.skipif(not ngspice_available(), reason="ngspice 미설치")
def test_sweep_reports_each_point(tmp_path):
    net = "* rc\nV1 in 0 dc 0 ac 1\nR1 in out 1k\nC1 out 0 100n\n.ac dec 10 10 1meg\n.end"
    got = []
    with progress_scope(lambda d, t, l: got.append((d, t, l))):
        r = sweep(net, "R1", [500.0, 1000.0, 2000.0], "fc WHEN vdb(out)=-3", runs_dir=tmp_path)
    assert r["points"] == 3
    assert [g[:2] for g in got] == [(1, 3), (2, 3), (3, 3)]
    assert got[1][2] == "R1=1 kΩ"
