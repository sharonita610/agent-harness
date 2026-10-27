"""평가 결과 JSON 여러 개를 모델 비교표(Markdown)로 만든다.
사용: uv run python scripts/compare_evals.py harness/evals/results/A.json harness/evals/results/B.json [--label-from tag]
라벨은 파일 이름의 tag 부분(마지막 _ 뒤) 을 쓴다.
"""
import json
import sys
from pathlib import Path

paths = [Path(p) for p in sys.argv[1:] if p.endswith(".json")]
if len(paths) < 1:
    sys.exit("결과 JSON 경로를 1개 이상 주세요")

runs = []
for p in paths:
    d = json.loads(p.read_text(encoding="utf-8"))
    label = p.stem.split("_", 2)[-1] if p.stem.count("_") >= 2 else p.stem
    runs.append((label, d))

types = []
for _, d in runs:
    for k in d["summary"]["by_type"]:
        if k not in types:
            types.append(k)

lines = ["| 모델 | 통과율 | " + " | ".join(types) + " | 평균 스텝 | 지연 p50 | 지연 p95 | 실행당 토큰 |", "|---|---|" + "---|" * len(types) + "---|---|---|---|"]
for label, d in runs:
    s = d["summary"]
    lines.append(f"| {label} | {s['pass_rate']:.0%} | " + " | ".join(f"{s['by_type'].get(t, 0):.0%}" for t in types) +
                 f" | {s['avg_steps']} | {s['p50_latency_s']}s | {s['p95_latency_s']}s | {s['tokens_per_run']:,} |")

# 태스크별 통과 비교
ids = [t["id"] for t in runs[0][1]["tasks"]]
lines += ["", "| 태스크 | " + " | ".join(l for l, _ in runs) + " |", "|---|" + "---|" * len(runs)]
for i in ids:
    row = []
    for _, d in runs:
        t = next((x for x in d["tasks"] if x["id"] == i), None)
        if t is None:
            row.append("-")
        else:
            failed = [k for k, v in t["runs"][-1]["checks"].items() if not v]
            row.append("✅" if t["pass_rate"] == 1 else "❌ " + ",".join(failed))
    lines.append(f"| {i} | " + " | ".join(row) + " |")
print("\n".join(lines))
