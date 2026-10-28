"""평가 러너: tasks.yaml 을 돌려 성적표(JSON + Markdown)를 남긴다.

비결정성 때문에 태스크당 n 회 반복해 통과율을 낸다. 승인 핸들러는 auto_deny 라서
변경 도구는 실제로 실행되지 않고 '승인을 요청했는지' 만 본다.
"""
from __future__ import annotations

import ast
import json
import math
import re
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..agent.loop import Agent
from ..agent.providers import make_provider
from ..observability.tracer import TraceCollector, maybe_export, write_jsonl
from ..packs import load_pack
from ..packs.enterprise.seed import DEFAULT_DB
from ..tools.permissions import auto_deny
from ..tools.registry import ToolRegistry

TASKS_PATH = Path(__file__).parent / "tasks.yaml"
RESULTS_DIR = Path(__file__).parent / "results"
PRICES_PATH = Path(__file__).parent / "prices.yaml"


def cost_usd(provider: str, prompt_tokens: int, completion_tokens: int) -> dict[str, float | None]:
    """실제 비용과 '호스팅했다면' 참고 비용(USD)."""
    prices = yaml.safe_load(PRICES_PATH.read_text(encoding="utf-8"))
    key = provider if provider in prices else ("local" if provider.startswith(("local", "compat")) else None)
    p = prices.get(key) if key else None
    if not p:
        return {"cost_usd": None, "cost_usd_if_hosted": None}
    actual = (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1e6
    ref = p.get("hosted_ref")
    hosted = (prompt_tokens * ref["input"] + completion_tokens * ref["output"]) / 1e6 if ref else actual
    return {"cost_usd": round(actual, 5), "cost_usd_if_hosted": round(hosted, 5)}


_SPACE_VARIANTS = {"\u202f": " ", "\u00a0": " ", "\u2009": " ", "\u2007": " ", "\u2011": "-", "\u2010": "-", "\u2212": "-"}  # 특수 공백·하이픈(‑ ‐ −) → 보통 글자


def normalize(text: str) -> str:
    """채점 전 정규화: 특수 공백(NNBSP 등)을 일반 공백으로, '5 %' → '5%', 마크다운 강조 제거."""
    for k, v in _SPACE_VARIANTS.items():
        text = text.replace(k, v)
    text = text.replace("**", "").replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"(\d)\s+%", r"\1%", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


def _num_in_answer(value: Any, answer: str) -> bool:
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not value.is_integer():
            return f"{value:g}" in answer.replace(",", "")
        n = int(value)
        plain = answer.replace(",", "")
        if re.search(rf"(?<!\d){n}(?!\d)", plain):
            return True
        if n >= 10000 and n % 10000 == 0 and re.search(rf"(?<!\d){n // 10000}\s*만", plain):
            return True
        if n >= 10000 and re.search(rf"(?<!\d){n // 10000}\s*만\s*{n % 10000}", plain):
            return True
        return False
    return str(value) in answer


_SAFE_FUNCS = {k: getattr(math, k) for k in ("sqrt", "log", "log10", "log2", "exp", "sin", "cos", "tan", "atan", "fabs", "pow")}
_SAFE_NAMES = {"pi": math.pi, "e": math.e, "ln9": math.log(9)}


def safe_eval(expr: str) -> float:
    """태스크 정답 공식(truth_expr)을 안전하게 계산한다: 사칙연산·거듭제곱·math 함수·pi 만 허용."""
    tree = ast.parse(expr, mode="eval")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            a, b = ev(node.left), ev(node.right)
            op = {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b, ast.Div: lambda: a / b, ast.Pow: lambda: a ** b}
            return op[type(node.op)]()
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Name) and node.id in _SAFE_NAMES:
            return _SAFE_NAMES[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCS and not node.keywords:
            return _SAFE_FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError(f"허용되지 않는 식: {ast.dump(node)[:60]}")

    return float(ev(tree))


_PREFIX = {"T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3, "K": 1e3, "m": 1e-3, "µ": 1e-6, "μ": 1e-6, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
_UNIT_WORDS = r"(?:Hz|hz|HZ|V|v|A|a|s|S|sec|F|H|Ω|ohm|Ohm|Ω|W|dB|db|°|도|%)"
_NUM_UNIT = re.compile(r"(?<![\w.])([-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?)\s*(?:([TGMkKmµμunpf])(?=" + _UNIT_WORDS + r"))?(" + _UNIT_WORDS + r")?")
_KO_UNITS = (("킬로헤르츠", 1e3), ("헤르츠", 1), ("마이크로초", 1e-6), ("밀리초", 1e-3), ("나노초", 1e-9), ("밀리암페어", 1e-3), ("볼트", 1))


def extract_numbers(answer: str) -> list[float]:
    """답변 속 숫자를 SI 접두사(kHz, µs, mA …)까지 해석해 기본 단위 값으로 모은다. 접두사 없는 원래 숫자도 함께 둔다."""
    text = answer.replace("\u202f", " ").replace("\u00a0", " ")
    text = re.sub(r"(?<=\d) (?=\d{3}(?![\d]))", "", text)  # 천 단위 공백 구분 "15 877.8" → "15877.8"
    out: list[float] = []
    for m in _NUM_UNIT.finditer(text):
        raw = m.group(1).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        out.append(v)
        if m.group(2):
            out.append(v * _PREFIX[m.group(2)])
        tail = text[m.end():m.end() + 6]
        for word, mult in _KO_UNITS:
            if tail.startswith(word):
                out.append(v * mult)
    return out


def within_tolerance(truth: float, answer: str, tolerance_pct: float) -> bool:
    """부호는 무시한다(전원 전류처럼 관례에 따라 음수로 보고되는 값이 있다)."""
    tol = abs(truth) * tolerance_pct / 100
    return any(abs(abs(n) - abs(truth)) <= tol for n in extract_numbers(answer))


def score(task: dict, res, truth: list[Any]) -> dict[str, Any]:
    answer = normalize(res.answer or "")
    truth = [normalize(v) if isinstance(v, str) else v for v in truth]
    executed = [t["name"] for t in res.tool_calls if t.get("status") == "ok"]
    approved_requests = [a["name"] for a in res.approvals]
    checks: dict[str, bool] = {}
    if truth and task.get("tolerance_pct"):
        checks["truth_within_tolerance"] = all(within_tolerance(float(v), answer, float(task["tolerance_pct"])) for v in truth)
    elif truth:
        checks["truth_in_answer"] = all(_num_in_answer(v, answer) for v in truth)
    if task.get("expect_tools_any"):
        checks["tools_any"] = any(t in executed for t in task["expect_tools_any"])
    if task.get("expect_tools_all"):
        checks["tools_all"] = all(t in executed for t in task["expect_tools_all"])
    if task.get("expect_calls"):
        def _call_ok(exp: dict) -> bool:
            for c in res.tool_calls:
                if c.get("name") != exp.get("name") or c.get("status") not in ("ok", None):
                    continue
                args = c.get("arguments") or {}
                preview = str(c.get("result_preview") or "")
                args_ok = all(str(v).lower() in str(args.get(k, "")).lower() for k, v in (exp.get("args_contains") or {}).items())
                result_ok = all((f'"{k}": "{v}"' in preview) or (f'"{k}":"{v}"' in preview) for k, v in (exp.get("result_contains") or {}).items())
                if args_ok and result_ok:
                    return True
            return False
        checks["calls"] = all(_call_ok(e) for e in task["expect_calls"])
    if task.get("forbid_tools_executed"):
        checks["no_forbidden_tools"] = not any(t in executed for t in task["forbid_tools_executed"])
    if task.get("expect_approval"):
        checks["approval_requested"] = all(t in approved_requests for t in task["expect_approval"])
    if task.get("forbid_approval_requested"):
        checks["no_forbidden_approval"] = not any(t in approved_requests for t in task["forbid_approval_requested"])
    if task.get("answer_contains_any"):
        checks["answer_contains"] = any(normalize(s) in answer for s in task["answer_contains_any"])
    if task.get("answer_contains_all"):
        checks["answer_contains_all"] = all(normalize(s) in answer for s in task["answer_contains_all"])
    if task.get("answer_not_contains"):
        checks["answer_clean"] = not any(normalize(s) in answer for s in task["answer_not_contains"])
    if task.get("answer_not_regex"):
        checks["answer_no_pattern"] = re.search(task["answer_not_regex"], answer) is None
    checks["finished"] = res.stopped_reason in ("final", "max_steps")
    return {"pass": all(checks.values()), "checks": checks, "truth": truth, "executed_tools": executed, "approvals": approved_requests}


def resolve(task: dict, conn: sqlite3.Connection | None) -> tuple[str, list[Any]]:
    """질문의 {변수} 를 채우고 정답을 만든다. truth_sql(DB) · truth_expr(공식, 팩 무관) · truth_value(상수) 순으로 본다."""
    question = task["question"]
    for key, sql in (task.get("params_sql") or {}).items():
        if conn is None:
            raise ValueError(f"{task['id']}: params_sql 은 DB 가 있는 팩에서만 쓸 수 있습니다")
        row = conn.execute(sql).fetchone()
        question = question.replace("{" + key + "}", str(row[0]) if row else "?")
    truth: list[Any] = []
    if task.get("truth_sql"):
        if conn is None:
            raise ValueError(f"{task['id']}: truth_sql 은 DB 가 있는 팩에서만 쓸 수 있습니다")
        row = conn.execute(task["truth_sql"]).fetchone()
        truth = list(row) if row else []
    elif task.get("truth_expr"):
        exprs = task["truth_expr"] if isinstance(task["truth_expr"], list) else [task["truth_expr"]]
        truth = [safe_eval(str(e)) for e in exprs]
    elif task.get("truth_value") is not None:
        tv = task["truth_value"]
        truth = list(tv) if isinstance(tv, list) else [tv]
    return question, truth


def run_eval(provider: str = "local", n: int = 1, tasks_path: Path | None = None, limit: int | None = None,
             tag: str | None = None, console=None, db_path: Path = DEFAULT_DB, prompt_version: str = "v1",
             pack: str | None = None) -> dict[str, Any]:
    pk = load_pack(pack)
    tasks = yaml.safe_load((tasks_path or pk.tasks_path).read_text(encoding="utf-8"))
    if limit:
        tasks = tasks[:limit]
    pack_db = (db_path if pk.name == "enterprise" else pk.db_path)
    conn = sqlite3.connect(f"file:{pack_db}?mode=ro", uri=True) if pack_db else None
    prov = make_provider(provider)
    registry = ToolRegistry(pk.build_tools(db_path=pack_db) if pack_db else pk.build_tools())
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"{provider.replace(':', '-').replace('/', '-')}{('_' + pk.name) if pk.name != 'enterprise' else ''}{('_' + tag) if tag else ''}"
    trace_dir = RESULTS_DIR / "traces" / f"{stamp}_{suffix}"
    collector_box: dict[str, TraceCollector] = {}
    system_prompt = pk.system_prompts[prompt_version]
    agent = Agent(prov, registry, system_prompt, approval_handler=auto_deny, max_steps=12,
                  on_event=lambda ev: collector_box["c"].on_event(ev) if "c" in collector_box else None)

    rows: list[dict[str, Any]] = []
    for task in tasks:
        question, truth = resolve(task, conn)
        runs = []
        for i in range(n):
            if task.get("seed_before") and pk.seed is not None:
                pk.seed()  # 상태 의존 문제(실행 기록 등)는 매번 같은 시드 상태에서 시작한다
            collector_box["c"] = TraceCollector(f"{task['id']}_run{i + 1}", question, {"provider": provider, "task": task["id"], "type": task["type"]})
            res = agent.run(question)
            collector_box["c"].finish(res)
            tpath = write_jsonl(collector_box["c"], trace_dir / f"{task['id']}_run{i + 1}.jsonl")
            maybe_export(collector_box["c"])
            sc = score(task, res, truth)
            runs.append({"run": i + 1, **sc, "answer": res.answer, "steps": res.steps, "latency_s": res.latency_s,
                         "prompt_tokens": res.usage.prompt_tokens, "completion_tokens": res.usage.completion_tokens,
                         "stopped": res.stopped_reason, "tool_calls": res.tool_calls,
                         "trace": str(tpath.relative_to(RESULTS_DIR)), "trace_summary": collector_box["c"].summary()})
            if console:
                mark = "[green]PASS[/green]" if sc["pass"] else "[red]FAIL[/red]"
                failed = [k for k, v in sc["checks"].items() if not v]
                console.print(f"{mark} {task['id']:4} run{i + 1} {res.steps}스텝 {res.latency_s:6.1f}s  {('실패: ' + ','.join(failed)) if failed else ''}")
        rows.append({"id": task["id"], "type": task["type"], "question": question, "pass_rate": sum(r["pass"] for r in runs) / n, "runs": runs})

    summary = _summarize(rows, provider=provider, model=getattr(prov, "model", "?"), n=n, tag=tag, prompt_version=prompt_version, pack=pk.name)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    base = RESULTS_DIR / f"{stamp}_{suffix}"
    base.with_suffix(".json").write_text(json.dumps({"summary": summary, "tasks": rows}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    base.with_suffix(".md").write_text(render_markdown(summary, rows), encoding="utf-8")
    if console:
        console.print(render_markdown(summary, rows))
        console.print(f"[dim]저장: {base}.json / .md[/dim]")
    return {"summary": summary, "tasks": rows, "path": str(base)}


def _summarize(rows, **meta) -> dict[str, Any]:
    all_runs = [r for t in rows for r in t["runs"]]
    by_type: dict[str, list[float]] = {}
    for t in rows:
        by_type.setdefault(t["type"], []).append(t["pass_rate"])
    total_prompt = sum(r["prompt_tokens"] for r in all_runs)
    total_completion = sum(r["completion_tokens"] for r in all_runs)
    return {
        **meta,
        "tasks": len(rows),
        "runs": len(all_runs),
        **cost_usd(meta.get("provider", "local"), total_prompt, total_completion),
        "pass_rate": round(sum(r["pass"] for r in all_runs) / max(1, len(all_runs)), 3),
        "by_type": {k: round(sum(v) / len(v), 3) for k, v in by_type.items()},
        "avg_steps": round(statistics.mean(r["steps"] for r in all_runs), 2) if all_runs else 0,
        "p50_latency_s": round(statistics.median(r["latency_s"] for r in all_runs), 1) if all_runs else 0,
        "p95_latency_s": round(sorted(r["latency_s"] for r in all_runs)[int(0.95 * (len(all_runs) - 1))], 1) if all_runs else 0,
        "tokens_per_run": round(statistics.mean(r["prompt_tokens"] + r["completion_tokens"] for r in all_runs)) if all_runs else 0,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# 평가 결과 · {summary['provider']} ({summary['model']}) · n={summary['n']}" + (f" · 팩 {summary['pack']}" if summary.get("pack") not in (None, "enterprise") else ""),
        "",
        f"- 통과율 {summary['pass_rate']:.0%} ({summary['runs']}회 실행, 태스크 {summary['tasks']}개) · 유형별 " + ", ".join(f"{k} {v:.0%}" for k, v in summary["by_type"].items()),
        f"- 평균 스텝 {summary['avg_steps']} · 지연 p50 {summary['p50_latency_s']}s / p95 {summary['p95_latency_s']}s · 실행당 토큰 {summary['tokens_per_run']}"
        + (f" · 비용 ${summary['cost_usd']:.4f}" if summary.get('cost_usd') is not None else "")
        + (f" (호스팅이면 ${summary['cost_usd_if_hosted']:.4f})" if summary.get('cost_usd_if_hosted') not in (None, summary.get('cost_usd')) else "")
        + (f" · 프롬프트 {summary['prompt_version']}" if summary.get('prompt_version') else ""),
        "",
        "| id | 유형 | 통과율 | 스텝 | 지연(s) | 실패 항목 | 답변(앞 80자) |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in rows:
        r0 = t["runs"][-1]
        failed = ",".join(k for k, v in r0["checks"].items() if not v) or "-"
        ans = (r0["answer"] or "").replace("\n", " ").replace("|", "/")[:80]
        lines.append(f"| {t['id']} | {t['type']} | {t['pass_rate']:.0%} | {r0['steps']} | {r0['latency_s']} | {failed} | {ans} |")
    return "\n".join(lines) + "\n"


def rescore(path: Path, tasks_path: Path | None = None) -> dict[str, Any]:
    """저장된 결과 JSON 을 현재 채점 로직으로 다시 채점해 같은 이름으로 덮어쓴다(모델 재호출 없음)."""
    from ..agent.types import AgentResult

    data = json.loads(path.read_text(encoding="utf-8"))
    if "tasks" not in data:  # 검색 평가·일치율 파일 등은 건너뛴다
        return {"skipped": path.name}
    tasks = {t["id"]: t for t in yaml.safe_load((tasks_path or load_pack(data["summary"].get("pack")).tasks_path).read_text(encoding="utf-8"))}
    for t in data["tasks"]:
        task = tasks[t["id"]]
        for r in t["runs"]:
            fake = AgentResult(answer=r["answer"] or "", steps=r["steps"], stopped_reason=r["stopped"],
                               tool_calls=r.get("tool_calls", []), approvals=[{"name": n} for n in r.get("approvals", [])])
            r.update(score(task, fake, r.get("truth", [])))
        t["pass_rate"] = sum(r["pass"] for r in t["runs"]) / len(t["runs"])
    meta = {k: data["summary"][k] for k in ("provider", "model", "n", "tag", "prompt_version", "pack") if k in data["summary"]}
    old_judge = data["summary"].get("judge")
    data["summary"] = {**_summarize(data["tasks"], **meta), "rescored_at": datetime.now().isoformat(timespec="seconds")}
    judged = [r for t in data["tasks"] for r in t["runs"] if r.get("judge")]
    if judged:  # 판정자 결과는 보존하고 규칙 채점과의 일치율만 다시 계산
        data["summary"]["judge"] = {**(old_judge or {}), "judged": len(judged),
                                    "pass_rate": round(sum(r["judge"]["pass"] for r in judged) / len(judged), 3),
                                    "agree_with_rules": round(sum(r["judge"]["pass"] == r["pass"] for r in judged) / len(judged), 3)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(render_markdown(data["summary"], data["tasks"]), encoding="utf-8")
    return data["summary"]
