"""규칙 채점 · LLM 판정자 · 사람 채점의 일치율과 Cohen's kappa. 사람 채점 JSON 은 grading 페이지가 내보낸 형식."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any


def kappa(a: list[bool], b: list[bool]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else round((po - pe) / (1 - pe), 3)


def sample_runs(data: dict[str, Any], n: int = 20, seed: int = 7) -> list[dict[str, Any]]:
    """유형별로 고르게, 통과/실패를 섞어 n개 뽑는다. 사람이 채점할 목록."""
    rows = [{"task_id": t["id"], "type": t["type"], "question": t["question"], "run": r["run"], "answer": r["answer"],
             "tools": r.get("executed_tools", []), "approvals": r.get("approvals", []), "truth": r.get("truth", []),
             "rule_pass": r["pass"], "judge_pass": r.get("judge", {}).get("pass")} for t in data["tasks"] for r in t["runs"]]
    rng = random.Random(seed)
    rng.shuffle(rows)
    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    out: list[dict[str, Any]] = []
    while len(out) < min(n, len(rows)):
        for k in list(by_type):
            if by_type[k] and len(out) < n:
                out.append(by_type[k].pop(0))
    return out


def compute_agreement(results: dict[str, Any], human: dict[str, Any]) -> dict[str, Any]:
    """human = {"grades": {"<task_id>:<run>": {"pass": bool, "note": str}}}"""
    grades = human.get("grades", {})
    triples = []
    for t in results["tasks"]:
        for r in t["runs"]:
            key = f"{t['id']}:{r['run']}"
            if key in grades and grades[key].get("pass") is not None:
                triples.append((bool(r["pass"]), r.get("judge", {}).get("pass"), bool(grades[key]["pass"]), key))
    n = len(triples)
    rules = [x[0] for x in triples]; judge = [x[1] for x in triples if x[1] is not None]; human_l = [x[2] for x in triples]
    judge_pairs = [(x[1], x[2]) for x in triples if x[1] is not None]
    out = {"n": n, "human_pass_rate": round(sum(human_l) / n, 3) if n else None,
           "rules_vs_human": {"agreement": round(sum(a == h for a, h in zip(rules, human_l)) / n, 3) if n else None, "kappa": kappa(rules, human_l)},
           "judge_vs_human": {"agreement": round(sum(j == h for j, h in judge_pairs) / len(judge_pairs), 3) if judge_pairs else None,
                              "kappa": kappa([j for j, _ in judge_pairs], [h for _, h in judge_pairs]) if judge_pairs else None},
           "disagreements": [k for r, j, h, k in triples if r != h or (j is not None and j != h)]}
    return out


def grading_page(results_path: Path, out_path: Path, n: int = 20) -> Path:
    """사람 채점 페이지(정적 HTML). 선택은 localStorage 에 남고 '내보내기' 로 JSON 다운로드."""
    data = json.loads(results_path.read_text(encoding="utf-8"))
    rows = sample_runs(data, n)
    label = results_path.stem
    html = ["""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>사람 채점</title>
<style>
:root{--bg:#f3f5f7;--card:#fff;--ink:#1b2430;--muted:#5b6673;--line:#d7dde3;--ok:#1f7a4d;--bad:#c8362b;--act:#0e6b63}
@media (prefers-color-scheme:dark){:root{--bg:#15181c;--card:#1d2126;--ink:#e8ebee;--muted:#a3acb6;--line:#2e343b;--ok:#5fc48f;--bad:#ef6b60;--act:#5cc2b6}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,"Apple SD Gothic Neo",sans-serif}main{max-width:900px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--muted);font-size:13.5px;margin-bottom:18px}.item{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:14px}
.q{font-weight:600}.crit{color:var(--muted);font-size:13.5px;margin:4px 0 8px}.ans{white-space:pre-wrap;border-left:3px solid var(--line);padding:6px 12px;margin:8px 0;font-size:14.5px}
.tools{font-size:13px;color:var(--muted)}.row{display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap}label{cursor:pointer}
input[type=text]{flex:1;min-width:200px;font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}
button{font:inherit;padding:9px 14px;border-radius:8px;border:1px solid var(--line);background:var(--act);color:#fff;cursor:pointer}.bar{position:sticky;top:0;background:var(--bg);padding:10px 0;display:flex;gap:12px;align-items:center;border-bottom:1px solid var(--line);margin-bottom:14px}
.hidden{display:none}.ok{color:var(--ok)}.bad{color:var(--bad)}
</style></head><body><main>
<h1>사람 채점</h1><div class="sub">""" + f"{label} · {len(rows)}개. 비서 답변만 보고 통과/실패를 고르세요. 규칙 채점과 판정자 결과는 '보기' 를 누르기 전엔 숨겨져 있습니다(편향 방지). 선택은 자동 저장됩니다." + """</div>
<div class="bar"><span id="progress"></span><button onclick="saveToServer()">서버에 저장</button><button onclick="exportJson()" style="background:transparent;color:var(--act)">JSON 다운로드</button><button onclick="toggleHidden()" style="background:transparent;color:var(--act)">규칙·판정자 결과 보기</button><span id="msg" style="color:var(--muted);font-size:13px"></span></div>
"""]
    for i, r in enumerate(rows, 1):
        key = f"{r['task_id']}:{r['run']}"
        crit = []
        if r["truth"]:
            crit.append(f"정답 값 {r['truth']}")
        crit.append(f"유형 {r['type']}")
        html.append(f'''<div class="item" data-key="{key}">
<div class="q">{i}. {r["question"]}</div><div class="crit">{" · ".join(crit)}</div>
<div class="ans">{(r["answer"] or "(빈 답변)").replace("<", "&lt;")}</div>
<div class="tools">도구: {", ".join(r["tools"]) or "없음"} · 승인 요청: {", ".join(r["approvals"]) or "없음"}</div>
<div class="row"><label><input type="radio" name="g-{key}" value="pass"> 통과</label><label><input type="radio" name="g-{key}" value="fail"> 실패</label><input type="text" placeholder="메모(선택)" data-note="{key}"></div>
<div class="hidden meta">규칙 채점: <span class="{'ok' if r['rule_pass'] else 'bad'}">{'통과' if r['rule_pass'] else '실패'}</span> · 판정자: <span class="{'ok' if r['judge_pass'] else 'bad'}">{'통과' if r['judge_pass'] else ('실패' if r['judge_pass'] is not None else '없음')}</span></div>
</div>''')
    html.append(f"""</main><script>
const STORE='grading:{label}';
let state=JSON.parse(localStorage.getItem(STORE)||'{{}}');
function save(){{localStorage.setItem(STORE,JSON.stringify(state));const n=document.querySelectorAll('.item').length;const done=Object.values(state).filter(v=>v.pass!==undefined&&v.pass!==null).length;document.getElementById('progress').textContent=`${{done}} / ${{n}} 채점됨`;}}
document.querySelectorAll('input[type=radio]').forEach(el=>{{const key=el.name.slice(2);if(state[key]&&((state[key].pass&&el.value==='pass')||(state[key].pass===false&&el.value==='fail')))el.checked=true;el.addEventListener('change',()=>{{state[key]={{...(state[key]||{{}}),pass:el.value==='pass'}};save();}});}});
document.querySelectorAll('input[data-note]').forEach(el=>{{const key=el.dataset.note;if(state[key]&&state[key].note)el.value=state[key].note;el.addEventListener('input',()=>{{state[key]={{...(state[key]||{{}}),note:el.value}};save();}});}});
function payload(){{return {{results:'{label}',graded_at:new Date().toISOString(),grades:state}};}}
function exportJson(){{const blob=new Blob([JSON.stringify(payload(),null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='human-{label}.json';a.click();}}
async function saveToServer(){{const m=document.getElementById('msg');try{{const r=await fetch(location.pathname.replace(/\\.html$/,''),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload())}});const d=await r.json();m.textContent=r.ok?`저장됨: ${{d.path}} (${{d.graded}}개)`:`저장 실패: ${{d.detail||r.status}}`;}}catch(e){{m.textContent='이 페이지는 정적 서버라 저장할 수 없습니다. http://127.0.0.1:8787/grading/… 에서 여세요.';}}}}
function toggleHidden(){{document.querySelectorAll('.meta').forEach(m=>m.classList.toggle('hidden'));}}
save();
</script></body></html>""")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(html), encoding="utf-8")
    return out_path
