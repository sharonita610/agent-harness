"""OpenAI 호환 LLM 서버 부하 테스트: 동시 요청 수별 TTFT·ITL·처리량 (ADR-030).

같은 프롬프트 묶음(한국어 8개, 300토큰 생성, temperature 0)을 동시 요청 수(--concurrency)별로 스트리밍으로 보내고
요청마다 첫 델타까지의 시간(TTFT), 델타 사이 간격(ITL), 생성 토큰 수를 기록한다. 토큰은 스트리밍 델타 하나를
한 토큰으로 센다(`content` 와 `reasoning_content` 모두: llama.cpp 는 gpt-oss 의 분석 채널을 `reasoning_content` 로 따로 흘린다).
표준 라이브러리만 쓴다. GPU 서버(vLLM·SGLang)에 파일 하나만 복사해 그대로 돌릴 수 있게.

사용:
  uv run python scripts/bench_llm.py --base http://127.0.0.1:8080/v1 --model local-llm \
      --concurrency 1 2 4 8 --json harness/evals/results/bench/20261101-llamacpp.json --label "llama.cpp 0.5.0"
  MLX:                --base http://127.0.0.1:8091/v1 --model mlx-community/gpt-oss-20b-MXFP4-Q8
  vLLM/SGLang(GPU):   --base http://<gpu>:8000/v1 --model openai/gpt-oss-20b [--api-key ...] [--concurrency 1 4 16 64]

측정 방식과 지표 정의: docs/decisions/030-llm-serving-metrics.md
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

SYSTEM = "당신은 회사 업무를 돕는 비서입니다. 한국어로 또박또박 답합니다."

# 도구 없이 설명형으로 300토큰을 채우는 질문. 레포의 세 팩(회사·회로·공정) 주제를 섞었다.
PROMPTS = [
    "재고 관리에서 안전재고를 정하는 기준을 세 가지로 나눠 설명해 주세요.",
    "지연 배송이 반복되는 거래처에 보낼 안내문 초안을 정중한 말투로 써 주세요.",
    "월별 매출 추세를 경영진에게 보고할 때 꼭 포함해야 할 항목을 정리해 주세요.",
    "LED 구동 회로에서 직렬 저항 값을 정하는 방법을 단계별로 설명해 주세요.",
    "공정 불량률이 갑자기 올라갔을 때 원인을 좁혀 가는 절차를 순서대로 적어 주세요.",
    "신입 사원에게 회사 결재 규정을 설명하는 짧은 안내문을 써 주세요.",
    "SQL 로 지난달 취소 주문 수를 구할 때 흔히 하는 실수와 피하는 방법을 설명해 주세요.",
    "분기 사업 계획서의 목차와 각 항목에 들어갈 내용을 간단히 적어 주세요.",
]

DONE = {"done": True}


# ---------------------------------------------------------------- 지표 계산 (순수 함수, tests/test_bench_llm.py)


def percentile(values: list[float], p: float) -> float | None:
    """선형 보간 백분위(numpy 기본과 같은 방식). 빈 목록이면 None."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def ms(x: float | None) -> float | None:
    return None if x is None else x * 1000.0


def request_metrics(t_start: float, token_times: list[float], t_end: float) -> dict:
    """요청 하나의 지표.

    t_start: 요청을 보낸 시각, token_times: 내용이 있는 델타가 도착한 시각들, t_end: 스트림이 닫힌 시각(모두 perf_counter 초).
    - TTFT = 첫 델타 - t_start (서버 쪽 큐 대기 + 프롬프트 처리 포함)
    - ITL  = 연이은 델타 사이 간격. p50 이 평소 속도, p95 가 끊김(다른 요청과의 배치 경합, 스케줄링)
    - decode_tps = (토큰 수 - 1) / (마지막 델타 - 첫 델타). 첫 토큰 이후의 순수 디코드 속도
    """
    n = len(token_times)
    gaps = [b - a for a, b in zip(token_times, token_times[1:])]
    span = token_times[-1] - token_times[0] if n > 1 else 0.0
    return {
        "ttft_s": (token_times[0] - t_start) if n else None,
        "tokens": n,
        "duration_s": t_end - t_start,
        "itl_p50_ms": ms(percentile(gaps, 50)),
        "itl_p95_ms": ms(percentile(gaps, 95)),
        "itl_max_ms": ms(max(gaps)) if gaps else None,
        "decode_tps": ((n - 1) / span) if span > 0 else None,
        "gaps_s": gaps,
    }


@dataclass
class RequestResult:
    index: int
    prompt_index: int
    concurrency: int
    offset_s: float  # 동시 수준 시작 기준 이 요청이 출발한 시각
    ttft_s: float | None = None
    tokens: int = 0
    tokens_usage: int | None = None  # 서버가 usage.completion_tokens 를 주면 교차 확인용
    duration_s: float = 0.0
    itl_p50_ms: float | None = None
    itl_p95_ms: float | None = None
    itl_max_ms: float | None = None
    decode_tps: float | None = None
    finish_reason: str | None = None
    error: str | None = None
    gaps_s: list[float] = field(default_factory=list)


def level_summary(concurrency: int, results: list[RequestResult], wall_s: float) -> dict:
    """동시 수준 하나의 요약. 처리량 = 전체 생성 토큰 / 벽시계 초, ITL 은 모든 요청의 간격을 한데 모아 백분위."""
    ok = [r for r in results if not r.error]
    ttfts = [r.ttft_s for r in ok if r.ttft_s is not None]
    gaps = [g for r in ok for g in r.gaps_s]
    durations = [r.duration_s for r in ok]
    decode = [r.decode_tps for r in ok if r.decode_tps]
    total_tokens = sum(r.tokens for r in ok)
    return {
        "concurrency": concurrency,
        "requests": len(results),
        "errors": len(results) - len(ok),
        "wall_s": wall_s,
        "total_tokens": total_tokens,
        "throughput_tps": (total_tokens / wall_s) if wall_s > 0 else None,
        "decode_tps_mean": statistics.fmean(decode) if decode else None,
        "ttft_p50_s": percentile(ttfts, 50),
        "ttft_p95_s": percentile(ttfts, 95),
        "ttft_max_s": max(ttfts) if ttfts else None,
        "itl_p50_ms": ms(percentile(gaps, 50)),
        "itl_p95_ms": ms(percentile(gaps, 95)),
        "itl_max_ms": ms(max(gaps)) if gaps else None,
        "latency_p50_s": percentile(durations, 50),
        "latency_p95_s": percentile(durations, 95),
    }


# ---------------------------------------------------------------- SSE 스트리밍


def parse_sse_line(raw: bytes) -> dict | None:
    """`data: {...}` 한 줄을 델타로. 데이터 줄이 아니면 None, `[DONE]` 이면 DONE."""
    line = raw.strip()
    if not line.startswith(b"data:"):
        return None
    payload = line[5:].strip()
    if payload == b"[DONE]":
        return DONE
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None
    choices = obj.get("choices") or []
    delta = (choices[0].get("delta") or {}) if choices else {}
    return {
        "content": delta.get("content") or "",
        "reasoning": delta.get("reasoning_content") or delta.get("reasoning") or "",
        "finish_reason": choices[0].get("finish_reason") if choices else None,
        "usage": obj.get("usage"),
    }


def stream_chat(
    base: str,
    model: str,
    messages: list[dict],
    *,
    max_tokens: int,
    temperature: float,
    timeout: float,
    api_key: str | None,
    extra_body: dict,
    include_usage: bool,
) -> tuple[float, list[float], float, str | None, int | None]:
    """스트리밍 요청 하나. (t_start, 델타 시각들, t_end, finish_reason, usage.completion_tokens) 를 돌려준다."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": True}
    if include_usage:
        body["stream_options"] = {"include_usage": True}
    body.update(extra_body)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions", data=json.dumps(body).encode(), headers=headers
    )
    token_times: list[float] = []
    finish = None
    usage_tokens = None
    t_start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            now = time.perf_counter()
            ev = parse_sse_line(raw)
            if ev is None:
                continue
            if ev is DONE:
                break
            if ev["content"] or ev["reasoning"]:
                token_times.append(now)
            if ev["finish_reason"]:
                finish = ev["finish_reason"]
            usage = ev["usage"] or {}
            if usage.get("completion_tokens") is not None:
                usage_tokens = usage["completion_tokens"]
    t_end = time.perf_counter()
    return t_start, token_times, t_end, finish, usage_tokens


def build_messages(prompt: str) -> list[dict]:
    # 요청마다 다른 꼬리표를 붙여 프롬프트 캐시가 TTFT 를 줄이지 못하게 한다(실제 트래픽은 매번 다른 질문이다).
    return [
        {"role": "system", "content": f"{SYSTEM} (요청 {uuid.uuid4().hex[:8]})"},
        {"role": "user", "content": prompt},
    ]


def _error_text(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return f"HTTP {e.code} {detail}".strip()
    return f"{type(e).__name__}: {e}"[:200]


# ---------------------------------------------------------------- 실행


def run_level(args, concurrency: int, prompts: list[str]) -> tuple[list[RequestResult], float]:
    results: list[RequestResult | None] = [None] * len(prompts)
    t0 = time.perf_counter()

    def worker(i: int) -> None:
        t_req = time.perf_counter()
        r = RequestResult(index=i, prompt_index=i % len(PROMPTS), concurrency=concurrency, offset_s=t_req - t0)
        try:
            t_start, times, t_end, finish, usage = stream_chat(
                args.base,
                args.model,
                build_messages(prompts[i]),
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                timeout=args.timeout,
                api_key=args.api_key,
                extra_body=args.extra_body,
                include_usage=not args.no_stream_usage,
            )
            m = request_metrics(t_start, times, t_end)
            r.ttft_s, r.tokens, r.duration_s = m["ttft_s"], m["tokens"], m["duration_s"]
            r.itl_p50_ms, r.itl_p95_ms, r.itl_max_ms = m["itl_p50_ms"], m["itl_p95_ms"], m["itl_max_ms"]
            r.decode_tps, r.gaps_s = m["decode_tps"], m["gaps_s"]
            r.finish_reason, r.tokens_usage = finish, usage
        except Exception as e:  # 한 요청의 실패가 수준 전체를 멈추지 않게. 오류는 결과에 남긴다
            r.error = _error_text(e)
            r.duration_s = time.perf_counter() - t_req
        results[i] = r

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        list(ex.map(worker, range(len(prompts))))
    wall = time.perf_counter() - t0
    return [r for r in results if r is not None], wall


def probe(base: str, path: str, timeout: float = 5.0) -> dict | None:
    """정보 수집용 GET. 실패하면 None (서버마다 있는 엔드포인트가 다르다)."""
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def fmt(x, nd=2, unit=""):
    return "-" if x is None else f"{x:.{nd}f}{unit}"


def render_table(levels: list[dict]) -> str:
    head = (
        "| 동시 | 요청 | 벽시계(s) | 생성 토큰 | 처리량(tok/s) | 요청당 디코드(tok/s) | TTFT p50/p95(s) | ITL p50/p95(ms) | 요청 지연 p50/p95(s) | 오류 |\n"
        "|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = [
        f"| {s['concurrency']} | {s['requests']} | {fmt(s['wall_s'], 1)} | {s['total_tokens']} | {fmt(s['throughput_tps'], 1)} "
        f"| {fmt(s['decode_tps_mean'], 1)} | {fmt(s['ttft_p50_s'])} / {fmt(s['ttft_p95_s'])} "
        f"| {fmt(s['itl_p50_ms'], 1)} / {fmt(s['itl_p95_ms'], 1)} | {fmt(s['latency_p50_s'], 1)} / {fmt(s['latency_p95_s'], 1)} | {s['errors']} |"
        for s in levels
    ]
    return "\n".join([head, *rows])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8080/v1", help="OpenAI 호환 베이스 URL (…/v1)")
    ap.add_argument("--model", default="local-llm")
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4, 8], help="동시 요청 수 목록")
    ap.add_argument("--repeat", type=int, default=1, help="수준마다 프롬프트 묶음(8개)을 몇 번 보낼지")
    ap.add_argument("--max-tokens", type=int, default=300)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--api-key", default=None, help="Authorization: Bearer (vLLM 등 키가 있는 서버)")
    ap.add_argument("--extra-body", default="{}", help='요청 본문에 더할 JSON, 예: \'{"reasoning_effort": "low"}\'')
    ap.add_argument("--no-stream-usage", action="store_true", help="stream_options.include_usage 를 보내지 않음")
    ap.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="측정 전 워밍업 요청 수(0 이면 없음). MLX 는 가중치를 지연 로드하므로 4~6 권장",
    )
    ap.add_argument("--label", default="", help="결과 파일에 남길 엔진 설명, 예: 'llama.cpp 0.5.0, -c 32768'")
    ap.add_argument("--json", dest="json_path", default=None, help="결과를 저장할 파일")
    ap.add_argument("--raw", action="store_true", help="요청별 ITL 간격 원본(gaps_s)까지 저장")
    ap.add_argument("-v", "--verbose", action="store_true", help="요청별 줄 출력")
    args = ap.parse_args(argv)
    args.extra_body = json.loads(args.extra_body)

    prompts = PROMPTS * args.repeat
    origin = args.base.rstrip("/").removesuffix("/v1")
    meta = {
        "base": args.base,
        "model": args.model,
        "label": args.label,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": {"platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version()},
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "prompts": len(prompts),
        "repeat": args.repeat,
        "extra_body": args.extra_body,
        "token_counting": "streaming delta with non-empty content or reasoning_content = 1 token",
    }
    props = probe(origin, "/props")  # llama.cpp 전용: 슬롯 수·컨텍스트
    if props:
        meta["server_props"] = {k: props.get(k) for k in ("total_slots", "model_alias", "build_info") if k in props}
        if isinstance(props.get("default_generation_settings"), dict):
            meta["server_props"]["n_ctx_per_slot"] = props["default_generation_settings"].get("n_ctx")
    models = probe(args.base.rstrip("/"), "/models")
    if models and isinstance(models.get("data"), list):
        meta["server_models"] = [m.get("id") for m in models["data"]][:5]

    print(
        f"[bench] {args.base} model={args.model} prompts={len(prompts)} max_tokens={args.max_tokens} levels={args.concurrency}"
    )
    if meta.get("server_props"):
        print(f"[bench] server props: {meta['server_props']}")

    meta["warmup_requests"] = args.warmup
    for i in range(args.warmup):
        try:
            t_start, times, t_end, finish, usage = stream_chat(
                args.base,
                args.model,
                build_messages(prompts[i % len(prompts)]),
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                timeout=args.timeout,
                api_key=args.api_key,
                extra_body=args.extra_body,
                include_usage=not args.no_stream_usage,
            )
        except Exception as e:  # 서버가 죽었거나(두 엔진 동시 기동 → Metal OOM) 모델을 아직 적재 중이면 여기서 멈춘다
            print(f"[bench] 워밍업 실패: {_error_text(e)}. 서버 상태를 확인하세요", file=sys.stderr)
            return 2
        w = request_metrics(t_start, times, t_end)
        w.pop("gaps_s")
        w["finish_reason"], w["tokens_usage"] = finish, usage
        if w["tokens"] == 0:
            print("[bench] 워밍업 응답에 토큰이 없습니다. 서버 상태를 확인하세요", file=sys.stderr)
            return 2
        meta["warmup"] = w  # 마지막 워밍업의 지표. 디코드 속도가 기대값에 왔는지 보고 측정을 시작한다
        print(
            f"[bench] warmup {i + 1}/{args.warmup}: ttft {fmt(w['ttft_s'])}s, {w['tokens']} tok (usage {usage}), "
            f"decode {fmt(w['decode_tps'], 1)} tok/s, finish={finish}"
        )

    levels: list[dict] = []
    all_requests: list[dict] = []
    for c in args.concurrency:
        results, wall = run_level(args, c, prompts)
        s = level_summary(c, results, wall)
        levels.append(s)
        print(
            f"[bench] c={c}: wall {wall:.1f}s, {s['total_tokens']} tok, throughput {fmt(s['throughput_tps'], 1)} tok/s, "
            f"decode/req {fmt(s['decode_tps_mean'], 1)} tok/s, TTFT p50 {fmt(s['ttft_p50_s'])}s p95 {fmt(s['ttft_p95_s'])}s, "
            f"ITL p50 {fmt(s['itl_p50_ms'], 1)}ms p95 {fmt(s['itl_p95_ms'], 1)}ms, errors {s['errors']}"
        )
        for r in results:
            if args.verbose:
                print(
                    f"    #{r.index} start+{r.offset_s:.2f}s ttft {fmt(r.ttft_s)}s tok {r.tokens} (usage {r.tokens_usage}) "
                    f"decode {fmt(r.decode_tps, 1)} itl p50/p95 {fmt(r.itl_p50_ms, 1)}/{fmt(r.itl_p95_ms, 1)}ms "
                    f"dur {r.duration_s:.1f}s finish={r.finish_reason}" + (f" ERROR {r.error}" if r.error else "")
                )
            d = asdict(r)
            if not args.raw:
                d.pop("gaps_s")
            all_requests.append(d)

    print()
    print(render_table(levels))

    if args.json_path:
        out = Path(args.json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps({"meta": meta, "levels": levels, "requests": all_requests}, ensure_ascii=False, indent=1)
        )
        print(f"\n[bench] saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
