"""llama-server 프롬프트 처리/생성 속도 측정. 캐시를 피하려고 매번 다른 접두를 붙인다.
사용: uv run python scripts/bench_prompt.py [prompt_tokens≈3000] [반복=2]
"""
import json
import sys
import time
import urllib.request
import uuid

URL = "http://127.0.0.1:8080/v1/chat/completions"
approx_tokens = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
repeat = int(sys.argv[2]) if len(sys.argv) > 2 else 2
filler = ("주문 번호 1023 은 2026년 9월 14일 접수되어 수도권 1창고에서 출고 예정이었으나 재고 부족으로 지연되었다. " * 400)[: approx_tokens * 2]

for i in range(repeat):
    body = {"model": "local-llm", "temperature": 0,
            "messages": [{"role": "system", "content": f"세션 {uuid.uuid4()}"}, {"role": "user", "content": filler + "\n\n위 내용을 한 문장으로 요약해."}],
            "max_tokens": 64}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    t = d.get("timings", {})
    print(f"run{i + 1}: prompt {t.get('prompt_n')} tok @ {t.get('prompt_per_second', 0):.0f} tok/s | gen {t.get('predicted_n')} tok @ {t.get('predicted_per_second', 0):.1f} tok/s | wall {time.perf_counter() - t0:.1f}s")
