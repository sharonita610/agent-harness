"""README 의 평가 결과 마커 사이를 최신 결과로 채운다. 팩별로 나눈다:
  enterprise → <!-- EVAL_RESULTS_START/END -->, spice → <!-- SPICE_EVAL_RESULTS_START/END -->
결과 JSON 이 2개 이상이면 모델 비교표(compare_evals.py), 1개면 그 결과의 표를 넣는다.
사용: uv run python scripts/update_readme_eval.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
MARKERS = {"enterprise": "EVAL_RESULTS", "spice": "SPICE_EVAL_RESULTS", "factory": "FACTORY_EVAL_RESULTS", "supervisor": "SUPERVISOR_EVAL_RESULTS"}
jsons = sorted(p for p in (root / "harness/evals/results").glob("*.json") if not p.name.startswith(("retrieval_", "agreement_", "human-")))
if not jsons:
    sys.exit("결과 파일 없음. 먼저 uv run harness eval")
by_pack: dict[str, list[Path]] = {}
for p in jsons:
    pack = json.loads(p.read_text(encoding="utf-8")).get("summary", {}).get("pack") or "enterprise"
    by_pack.setdefault(pack, []).append(p)

readme = root / "README.md"
text = readme.read_text(encoding="utf-8")
for pack, files in by_pack.items():
    marker = MARKERS.get(pack)
    if not marker or f"<!-- {marker}_START -->" not in text:
        print(f"{pack}: README 에 마커 없음, 건너뜀")
        continue
    if len(files) >= 2:
        body = subprocess.run([sys.executable, str(root / "scripts/compare_evals.py"), *map(str, files)], capture_output=True, text=True, check=True).stdout.strip()
        src = ", ".join(f"`{p.relative_to(root)}`" for p in files)
    else:
        body = files[0].with_suffix(".md").read_text(encoding="utf-8").split("\n", 1)[1].strip()
        src = f"`{files[0].relative_to(root)}`"
    block = f"<!-- {marker}_START -->\n_출처: {src}_ · 같은 문제, n = 태스크당 반복 횟수\n\n{body}\n<!-- {marker}_END -->"
    text = re.sub(rf"<!-- {marker}_START -->.*?<!-- {marker}_END -->", lambda _: block, text, flags=re.S)
    print(f"{pack}: README 갱신 ({len(files)}개 결과)")
readme.write_text(text, encoding="utf-8")
