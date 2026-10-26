"""평가 러너: tasks.yaml 을 돌려 성적표(JSON + Markdown)를 남긴다.

비결정성 때문에 태스크당 n 회 반복해 통과율을 낸다. 승인 핸들러는 auto_deny 라서
변경 도구는 실제로 실행되지 않고 '승인을 요청했는지' 만 본다.
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..agent.loop import Agent
from ..agent.providers import make_provider
from ..packs.enterprise.seed import DEFAULT_DB
from ..packs.enterprise.tools import SYSTEM_PROMPT, build_tools
from ..tools.permissions import auto_deny
from ..tools.registry import ToolRegistry

TASKS_PATH = Path(__file__).parent / "tasks.yaml"
RESULTS_DIR = Path(__file__).parent / "results"


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


def score(task: dict, res, truth: list[Any]) -> dict[str, Any]:
    answer = res.answer or ""
    executed = [t["name"] for t in res.tool_calls if t.get("status") == "ok"]
    approved_requests = [a["name"] for a in res.approvals]
    checks: dict[str, bool] = {}
    if truth:
        checks["truth_in_answer"] = all(_num_in_answer(v, answer) for v in truth)
    if task.get("expect_tools_any"):
        checks["tools_any"] = any(t in executed for t in task["expect_tools_any"])
    if task.get("expect_tools_all"):
        checks["tools_all"] = all(t in executed for t in task["expect_tools_all"])
    if task.get("forbid_tools_executed"):
        checks["no_forbidden_tools"] = not any(t in executed for t in task["forbid_tools_executed"])
    if task.get("expect_approval"):
        checks["approval_requested"] = all(t in approved_requests for t in task["expect_approval"])
    if task.get("answer_contains_any"):
        checks["answer_contains"] = any(s in answer for s in task["answer_contains_any"])
    if task.get("answer_not_contains"):
        checks["answer_clean"] = not any(s in answer for s in task["answer_not_contains"])
    if task.get("answer_not_regex"):
        checks["answer_no_pattern"] = re.search(task["answer_not_regex"], answer) is None
    checks["finished"] = res.stopped_reason in ("final", "max_steps")
    return {"pass": all(checks.values()), "checks": checks, "truth": truth, "executed_tools": executed, "approvals": approved_requests}


def resolve(task: dict, conn: sqlite3.Connection) -> tuple[str, list[Any]]:
    question = task["question"]
    for key, sql in (task.get("params_sql") or {}).items():
        row = conn.execute(sql).fetchone()
        question = question.replace("{" + key + "}", str(row[0]) if row else "?")
    truth: list[Any] = []
    if task.get("truth_sql"):
        row = conn.execute(task["truth_sql"]).fetchone()
        truth = list(row) if row else []
    return question, truth


def run_eval(provider: str = "local", n: int = 1, tasks_path: Path | None = None, limit: int | None = None,
             tag: str | None = None, console=None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    tasks = yaml.safe_load((tasks_path or TASKS_PATH).read_text(encoding="utf-8"))
    if limit:
        tasks = tasks[:limit]
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    prov = make_provider(provider)
    registry = ToolRegistry(build_tools(db_path))
    agent = Agent(prov, registry, SYSTEM_PROMPT, approval_handler=auto_deny, max_steps=12)

    rows: list[dict[str, Any]] = []
    for task in tasks:
        question, truth = resolve(task, conn)
        runs = []
        for i in range(n):
            res = agent.run(question)
            sc = score(task, res, truth)
            runs.append({"run": i + 1, **sc, "answer": res.answer, "steps": res.steps, "latency_s": res.latency_s,
                         "prompt_tokens": res.usage.prompt_tokens, "completion_tokens": res.usage.completion_tokens,
                         "stopped": res.stopped_reason, "tool_calls": res.tool_calls})
            if console:
                mark = "[green]PASS[/green]" if sc["pass"] else "[red]FAIL[/red]"
                failed = [k for k, v in sc["checks"].items() if not v]
                console.print(f"{mark} {task['id']:4} run{i + 1} {res.steps}스텝 {res.latency_s:6.1f}s  {('실패: ' + ','.join(failed)) if failed else ''}")
        rows.append({"id": task["id"], "type": task["type"], "question": question, "pass_rate": sum(r["pass"] for r in runs) / n, "runs": runs})

    summary = _summarize(rows, provider=provider, model=getattr(prov, "model", "?"), n=n, tag=tag)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = RESULTS_DIR / f"{stamp}_{provider.replace(':', '-').replace('/', '-')}{('_' + tag) if tag else ''}"
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
    return {
        **meta,
        "tasks": len(rows),
        "runs": len(all_runs),
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
        f"# 평가 결과 · {summary['provider']} ({summary['model']}) · n={summary['n']}",
        "",
        f"- 통과율 {summary['pass_rate']:.0%} ({summary['runs']}회 실행, 태스크 {summary['tasks']}개) · 유형별 " + ", ".join(f"{k} {v:.0%}" for k, v in summary["by_type"].items()),
        f"- 평균 스텝 {summary['avg_steps']} · 지연 p50 {summary['p50_latency_s']}s / p95 {summary['p95_latency_s']}s · 실행당 토큰 {summary['tokens_per_run']}",
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
