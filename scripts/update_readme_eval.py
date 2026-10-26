"""가장 최근 평가 결과(.md)를 README 의 EVAL_RESULTS 마커 사이에 끼워 넣는다.
사용: uv run python scripts/update_readme_eval.py [결과.md]
"""
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
results = sorted((root / "harness/evals/results").glob("*.md"))
src = Path(sys.argv[1]) if len(sys.argv) > 1 else (results[-1] if results else None)
if not src:
    sys.exit("결과 파일 없음. 먼저 uv run harness eval")
body = src.read_text(encoding="utf-8").split("\n", 1)[1].strip()  # 첫 줄(# 제목) 제거
block = f"<!-- EVAL_RESULTS_START -->\n_출처: `{src.relative_to(root)}`_\n\n{body}\n<!-- EVAL_RESULTS_END -->"
readme = root / "README.md"
text = readme.read_text(encoding="utf-8")
new = re.sub(r"<!-- EVAL_RESULTS_START -->.*?<!-- EVAL_RESULTS_END -->", lambda _: block, text, flags=re.S)
readme.write_text(new, encoding="utf-8")
print(f"README 갱신: {src.name}")
