"""scripts/bench_llm.py 의 지표 계산을 가짜 타임스탬프로 검증한다 (ADR-030)."""

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "bench_llm", Path(__file__).resolve().parents[1] / "scripts" / "bench_llm.py"
)
bench = importlib.util.module_from_spec(_spec)
sys.modules["bench_llm"] = bench  # dataclasses 가 문자열 주석을 풀 때 sys.modules 에서 모듈을 찾는다
_spec.loader.exec_module(bench)


def test_percentile_linear_interpolation():
    assert bench.percentile([], 50) is None
    assert bench.percentile([7.0], 95) == 7.0
    assert bench.percentile([5, 1, 3, 2, 4], 50) == 3
    assert bench.percentile([1, 2, 3, 4, 5], 95) == 4.8  # 0.95*(5-1)=3.8 → 4 + 0.8*(5-4)


def test_request_metrics_from_fake_timestamps():
    # 요청 10.0초에 출발, 첫 델타 10.10, 이후 20ms 간격으로 20개 더 → 21토큰, 스트림은 10.60 에 닫힘
    t_start = 10.0
    times = [10.10 + 0.02 * i for i in range(21)]
    m = bench.request_metrics(t_start, times, 10.60)
    assert abs(m["ttft_s"] - 0.10) < 1e-9
    assert m["tokens"] == 21
    assert abs(m["itl_p50_ms"] - 20.0) < 1e-6 and abs(m["itl_p95_ms"] - 20.0) < 1e-6
    assert abs(m["decode_tps"] - 50.0) < 1e-6  # (21-1) / (0.02*20)
    assert abs(m["duration_s"] - 0.60) < 1e-9
    # 델타가 하나도 없으면(오류·빈 응답) TTFT·디코드는 None, 토큰 0
    empty = bench.request_metrics(0.0, [], 1.0)
    assert empty["ttft_s"] is None and empty["tokens"] == 0 and empty["decode_tps"] is None


def test_level_summary_pools_itl_and_computes_throughput():
    # 요청 A: 정상 10토큰(20ms). 요청 B: 큐 대기로 TTFT 2초 + 한 번 500ms 끊김. 벽시계 4초
    a = bench.RequestResult(index=0, prompt_index=0, concurrency=2, offset_s=0.0)
    ma = bench.request_metrics(0.0, [0.1 + 0.02 * i for i in range(10)], 0.3)
    a.ttft_s, a.tokens, a.gaps_s, a.decode_tps, a.duration_s = (
        ma["ttft_s"],
        ma["tokens"],
        ma["gaps_s"],
        ma["decode_tps"],
        0.3,
    )
    b = bench.RequestResult(index=1, prompt_index=1, concurrency=2, offset_s=0.0)
    tb = [2.0 + 0.02 * i for i in range(10)]
    tb[5:] = [t + 0.5 for t in tb[5:]]
    mb = bench.request_metrics(0.0, tb, 3.0)
    b.ttft_s, b.tokens, b.gaps_s, b.decode_tps, b.duration_s = (
        mb["ttft_s"],
        mb["tokens"],
        mb["gaps_s"],
        mb["decode_tps"],
        3.0,
    )
    bad = bench.RequestResult(index=2, prompt_index=2, concurrency=2, offset_s=0.0, error="HTTP 500")

    s = bench.level_summary(2, [a, b, bad], wall_s=4.0)
    assert s["requests"] == 3 and s["errors"] == 1
    assert s["total_tokens"] == 20 and abs(s["throughput_tps"] - 5.0) < 1e-9  # 20 tok / 4 s
    assert abs(s["ttft_p50_s"] - 1.05) < 1e-9 and abs(s["ttft_max_s"] - 2.0) < 1e-9
    assert abs(s["itl_p50_ms"] - 20.0) < 1e-6  # 18개 간격 중 17개가 20ms
    # 18개 간격 중 끊김 하나(520ms): p95 는 16.15번째 자리 → 20 + 0.15*(520-20) = 95ms, max 는 520ms
    assert abs(s["itl_p95_ms"] - 95.0) < 1e-6 and abs(s["itl_max_ms"] - 520.0) < 1e-6
    assert abs(s["latency_p95_s"] - (0.3 + 0.95 * 2.7)) < 1e-9


def test_parse_sse_line_counts_content_and_reasoning():
    assert bench.parse_sse_line(b": keep-alive\n") is None
    assert bench.parse_sse_line(b"data: [DONE]\n") is bench.DONE
    ev = bench.parse_sse_line(b'data: {"choices":[{"delta":{"reasoning_content":"We"},"finish_reason":null}]}\n')
    assert ev["reasoning"] == "We" and ev["content"] == "" and ev["finish_reason"] is None
    line = 'data: {"choices":[{"delta":{"content":"안녕"},"finish_reason":"stop"}],"usage":{"completion_tokens":300}}\n'
    ev = bench.parse_sse_line(line.encode())
    assert ev["content"] == "안녕" and ev["finish_reason"] == "stop" and ev["usage"]["completion_tokens"] == 300
    assert (
        bench.parse_sse_line(b'data: {"choices":[],"usage":{"completion_tokens":300}}\n')["usage"]["completion_tokens"]
        == 300
    )
