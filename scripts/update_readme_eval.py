"""README 의 EVAL_RESULTS 마커 사이를 최신 평가 결과로 채운다.
결과 JSON 이 2개 이상이면 모델 비교표(compare_evals.py), 1개면 그 결과의 표를 넣는다.
사용: uv run python scripts/update_readme_eval.py
"""
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
jsons = sorted((root / "harness/evals/results").glob("*.json"))
if not jsons:
    sys.exit("결과 파일 없음. 먼저 uv run harness eval")
if len(jsons) >= 2:
    body = subprocess.run([sys.executable, str(root / "scripts/compare_evals.py"), *map(str, jsons)], capture_output=True, text=True, check=True).stdout.strip()
    src = ", ".join(f"`{p.relative_to(root)}`" for p in jsons)
else:
    body = jsons[0].with_suffix(".md").read_text(encoding="utf-8").split("\n", 1)[1].strip()
    src = f"`{jsons[0].relative_to(root)}`"
block = f"<!-- EVAL_RESULTS_START -->\n_출처: {src}_ · 태스크당 n=1, 같은 16문제, 같은 프롬프트\n\n{body}\n<!-- EVAL_RESULTS_END -->"
readme = root / "README.md"
text = readme.read_text(encoding="utf-8")
readme.write_text(re.sub(r"<!-- EVAL_RESULTS_START -->.*?<!-- EVAL_RESULTS_END -->", lambda _: block, text, flags=re.S), encoding="utf-8")
print(f"README 갱신: {len(jsons)}개 결과")
