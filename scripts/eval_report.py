"""평가 결과 JSON 들을 하나의 HTML 리포트로 만든다 (외부 의존 없음).
사용: uv run python scripts/eval_report.py  → harness/evals/results/report.html
"""
import html
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
res_dir = root / "harness/evals/results"
jsons = sorted(p for p in res_dir.glob("*.json") if not p.name.startswith(("retrieval_", "agreement_", "human-")))
runs = []
for p in jsons:
    d = json.loads(p.read_text(encoding="utf-8"))
    label = p.stem.split("_", 2)[-1] if p.stem.count("_") >= 2 else p.stem
    runs.append((label, d))

TYPE_KO = {"query": "조회", "mixed": "복합", "doc": "규정", "write": "변경→승인", "trap": "함정"}
types = []
for _, d in runs:
    for k in d["summary"]["by_type"]:
        if k not in types:
            types.append(k)


def esc(x):
    return html.escape(str(x))


def pct(v):
    return f"{v:.0%}"


parts = []
parts.append("""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>에이전트 평가 리포트</title>
<style>
:root{--bg:#fafaf7;--card:#fff;--ink:#1f1f1c;--muted:#6b6a64;--line:#e6e4dc;--ok:#1d9e75;--bad:#d85a30;--accent:#534ab7}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--card:#1f1f1c;--ink:#ecebe4;--muted:#9c9a92;--line:#33322e;--ok:#5dcaa5;--bad:#f0997b;--accent:#afa9ec}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;font-weight:600;margin:0 0 4px}h2{font-size:17px;font-weight:600;margin:32px 0 10px}
.sub{color:var(--muted);margin-bottom:20px}table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;font-size:14px}th{color:var(--muted);font-weight:500;background:transparent}
tr:last-child td{border-bottom:0}.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}.num{text-align:right;font-variant-numeric:tabular-nums}
details{margin:6px 0}summary{cursor:pointer;color:var(--accent)}pre{white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;font-size:13px;margin:6px 0}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid var(--line);font-size:12px;color:var(--muted);margin-right:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:12px 0 4px}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card b{font-size:26px;font-weight:600;display:block}.card span{color:var(--muted);font-size:13px}
</style></head><body><main>
<h1>에이전트 평가 리포트</h1><div class="sub">회사 데이터 비서 · 같은 16문제 · 같은 프롬프트 · n = 태스크당 반복 횟수</div>""")

parts.append("<h2>모델 비교</h2><table><tr><th>모델</th><th>n</th><th class=num>통과율</th>" + "".join(f"<th class=num>{TYPE_KO.get(t, t)}</th>" for t in types) + "<th class=num>평균 스텝</th><th class=num>지연 p50</th><th class=num>지연 p95</th><th class=num>실행당 토큰</th><th>실행 시각</th></tr>")
for label, d in runs:
    s = d["summary"]
    parts.append(f"<tr><td><b>{esc(label)}</b><br><span class=pill>{esc(s.get('model', '?'))}</span></td><td class=num>{s.get('n', 1)}</td><td class=num><b>{pct(s['pass_rate'])}</b></td>" +
                 "".join(f"<td class=num>{pct(s['by_type'].get(t, 0))}</td>" for t in types) +
                 f"<td class=num>{s['avg_steps']}</td><td class=num>{s['p50_latency_s']}s</td><td class=num>{s['p95_latency_s']}s</td><td class=num>{s['tokens_per_run']:,}</td><td>{esc(s.get('generated_at', ''))[:16]}</td></tr>")
parts.append("</table>")

ids = [t["id"] for t in runs[0][1]["tasks"]] if runs else []
parts.append("<h2>태스크별 통과율</h2><table><tr><th>태스크</th><th>유형</th><th>질문</th>" + "".join(f"<th class=num>{esc(l)}</th>" for l, _ in runs) + "</tr>")
for i in ids:
    base = next(t for t in runs[0][1]["tasks"] if t["id"] == i)
    cells = []
    for _, d in runs:
        t = next((x for x in d["tasks"] if x["id"] == i), None)
        if t is None:
            cells.append("<td class=num>-</td>")
        else:
            n = len(t["runs"]); k = sum(r["pass"] for r in t["runs"])
            cls = "ok" if k == n else "bad"
            failed = sorted({c for r in t["runs"] for c, v in r["checks"].items() if not v})
            cells.append(f"<td class=num><span class={cls}>{k}/{n}</span>" + (f"<br><small>{esc(','.join(failed))}</small>" if failed else "") + "</td>")
    parts.append(f"<tr><td>{esc(i)}</td><td>{TYPE_KO.get(base['type'], base['type'])}</td><td>{esc(base['question'])}</td>{''.join(cells)}</tr>")
parts.append("</table>")

parts.append("<h2>답변 원문 (펼쳐 보기)</h2>")
for label, d in runs:
    parts.append(f"<details><summary><b>{esc(label)}</b> · {d['summary'].get('model', '')} · 통과율 {pct(d['summary']['pass_rate'])}</summary>")
    for t in d["tasks"]:
        for r in t["runs"]:
            cls = "ok" if r["pass"] else "bad"
            failed = [c for c, v in r["checks"].items() if not v]
            tools = " → ".join(r.get("executed_tools", [])) or "(도구 호출 없음)"
            parts.append(f"<details><summary><span class={cls}>{'PASS' if r['pass'] else 'FAIL'}</span> {esc(t['id'])} run{r['run']} · {r['steps']}스텝 · {r['latency_s']}s" + (f" · 실패: {esc(','.join(failed))}" if failed else "") + f"</summary><div><span class=pill>{esc(tools)}</span>" + (f"<span class=pill>정답: {esc(r['truth'])}</span>" if r.get('truth') else "") + f"</div><pre>{esc(r['answer'])}</pre></details>")
    parts.append("</details>")
parts.append("</main></body></html>")
out = res_dir / "report.html"
out.write_text("\n".join(parts), encoding="utf-8")
print(f"{out} ({len(runs)}개 결과)")
