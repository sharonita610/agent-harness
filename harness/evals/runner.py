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
from ..agent.types import Usage
from ..observability.redact import enabled as redact_enabled
from ..observability.redact import maybe_redact, redact_text
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


_SPACE_VARIANTS = {
    "\u202f": " ",
    "\u00a0": " ",
    "\u2009": " ",
    "\u2007": " ",
    "\u2011": "-",
    "\u2010": "-",
    "\u2212": "-",
}  # 특수 공백·하이픈(‑ ‐ −) → 보통 글자


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


_SAFE_FUNCS = {
    k: getattr(math, k) for k in ("sqrt", "log", "log10", "log2", "exp", "sin", "cos", "tan", "atan", "fabs", "pow")
}
_SAFE_NAMES = {"pi": math.pi, "e": math.e, "ln9": math.log(9)}


def safe_eval(expr: str) -> float:
    """태스크 정답 공식(truth_expr)을 안전하게 계산한다: 사칙연산·거듭제곱·math 함수·pi 만 허용."""
    tree = ast.parse(expr, mode="eval")

    def eval_node(node):
        if isinstance(node, ast.Expression):
            return eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            a, b = eval_node(node.left), eval_node(node.right)
            op = {
                ast.Add: lambda: a + b,
                ast.Sub: lambda: a - b,
                ast.Mult: lambda: a * b,
                ast.Div: lambda: a / b,
                ast.Pow: lambda: a**b,
            }
            return op[type(node.op)]()
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = eval_node(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Name) and node.id in _SAFE_NAMES:
            return _SAFE_NAMES[node.id]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _SAFE_FUNCS
            and not node.keywords
        ):
            return _SAFE_FUNCS[node.func.id](*[eval_node(a) for a in node.args])
        raise ValueError(f"허용되지 않는 식: {ast.dump(node)[:60]}")

    return float(eval_node(tree))


_PREFIX = {
    "T": 1e12,
    "G": 1e9,
    "M": 1e6,
    "k": 1e3,
    "K": 1e3,
    "m": 1e-3,
    "µ": 1e-6,
    "μ": 1e-6,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}
_UNIT_WORDS = r"(?:Hz|hz|HZ|V|v|A|a|s|S|sec|F|H|Ω|ohm|Ohm|Ω|W|dB|db|°|도|%)"
_NUM_UNIT = re.compile(
    r"(?<![\w.])([-+]?\d[\d,]*\.?\d*(?:[eE][-+]?\d+)?)\s*(?:([TGMkKmµμunpf])(?="
    + _UNIT_WORDS
    + r"))?("
    + _UNIT_WORDS
    + r")?"
)
_KO_UNITS = (
    ("킬로헤르츠", 1e3),
    ("헤르츠", 1),
    ("마이크로초", 1e-6),
    ("밀리초", 1e-3),
    ("나노초", 1e-9),
    ("밀리암페어", 1e-3),
    ("볼트", 1),
)


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
        tail = text[m.end() : m.end() + 6]
        for word, mult in _KO_UNITS:
            if tail.startswith(word):
                out.append(v * mult)
    return out


def within_tolerance(truth: float, answer: str, tolerance_pct: float) -> bool:
    """부호는 무시한다(전원 전류처럼 관례에 따라 음수로 보고되는 값이 있다)."""
    tol = abs(truth) * tolerance_pct / 100
    return any(abs(abs(n) - abs(truth)) <= tol for n in extract_numbers(answer))


def _arg_has(actual: Any, expected: Any) -> bool:
    """기대값이 목록이면 그중 하나라도 들어 있으면 된다(스테이션을 '화성' 으로도 '16' 으로도 줄 수 있다)."""
    options = expected if isinstance(expected, list) else [expected]
    return any(str(v).lower() in str(actual).lower() for v in options)


def score(task: dict, res, truth: list[Any]) -> dict[str, Any]:
    answer = normalize(res.answer or "")
    truth = [normalize(v) if isinstance(v, str) else v for v in truth]
    executed = [t["name"] for t in res.tool_calls if t.get("status") == "ok"]
    # "도구를 썼는가" 는 실행 오류로 끝난 호출도 센다(없는 주문을 get_order 로 확인하면 오류가 답이다). 인자 오류·금지는 안 센다
    consulted = [t["name"] for t in res.tool_calls if t.get("status") in ("ok", "exec_error")]
    approved_requests = [a["name"] for a in res.approvals]
    checks: dict[str, bool] = {}
    if truth and task.get("tolerance_pct"):
        checks["truth_within_tolerance"] = all(
            within_tolerance(float(v), answer, float(task["tolerance_pct"])) for v in truth
        )
    elif truth:
        checks["truth_in_answer"] = all(_num_in_answer(v, answer) for v in truth)
    if task.get("expect_tools_any"):
        checks["tools_any"] = any(t in consulted for t in task["expect_tools_any"])
    if task.get("expect_tools_all"):
        checks["tools_all"] = all(t in consulted for t in task["expect_tools_all"])
    if task.get("expect_calls"):

        def _call_ok(exp: dict) -> bool:
            # name 은 문자열 하나, "a|b", 또는 목록. 같은 일을 하는 도구가 둘일 때(delegate 와 delegate_many) 어느 쪽이든 인정한다
            names = exp.get("name")
            names = [str(x) for x in names] if isinstance(names, list) else str(names or "").split("|")
            for c in res.tool_calls:
                if c.get("name") not in names or c.get("status") not in ("ok", None):
                    continue
                args = c.get("arguments") or {}
                preview = str(c.get("result_preview") or "")
                args_ok = all(
                    str(v).lower() in str(args.get(k, "")).lower() for k, v in (exp.get("args_contains") or {}).items()
                )
                result_ok = all(
                    (f'"{k}": "{v}"' in preview) or (f'"{k}":"{v}"' in preview)
                    for k, v in (exp.get("result_contains") or {}).items()
                )
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
    if task.get("expect_approval_args"):
        # 승인 요청의 인자까지 본다(업무 흐름: "계산한 비율을 메모에 남겨라" → create_note 의 note 에 그 비율이 있어야)
        def _approval_ok(exp: dict) -> bool:
            for a in res.approvals:
                if a.get("name") != exp.get("name"):
                    continue
                args = a.get("arguments") or {}
                if all(_arg_has(args.get(k, ""), v) for k, v in (exp.get("args_contains") or {}).items()):
                    return True
            return False

        checks["approval_args"] = all(_approval_ok(e) for e in task["expect_approval_args"])
    if task.get("answer_contains_any"):
        checks["answer_contains"] = any(normalize(s) in answer for s in task["answer_contains_any"])
    if task.get("answer_contains_all"):
        checks["answer_contains_all"] = all(normalize(s) in answer for s in task["answer_contains_all"])
    if task.get("answer_not_contains"):
        checks["answer_clean"] = not any(normalize(s) in answer for s in task["answer_not_contains"])
    if task.get("answer_not_regex"):
        checks["answer_no_pattern"] = re.search(task["answer_not_regex"], answer) is None
    if task.get("answer_regex_all"):
        checks["answer_patterns"] = all(re.search(p, answer) is not None for p in task["answer_regex_all"])
    checks["finished"] = res.stopped_reason in ("final", "max_steps")
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "truth": truth,
        "executed_tools": executed,
        "approvals": approved_requests,
    }


def _fill(obj: Any, params: dict[str, str]) -> Any:
    """태스크 안의 모든 문자열에서 {변수} 를 채운다(질문·대화 턴·기대 인자·truth_sql 전부)."""
    if isinstance(obj, str):
        for k, v in params.items():
            obj = obj.replace("{" + k + "}", v)
        return obj
    if isinstance(obj, list):
        return [_fill(x, params) for x in obj]
    if isinstance(obj, dict):
        return {k: _fill(v, params) for k, v in obj.items()}
    return obj


def prepare(task: dict, conn: sqlite3.Connection | None) -> tuple[dict, str, list[Any]]:
    """params_sql 로 {변수} 를 채운 태스크 사본, 표시용 질문(다중 턴이면 ' → ' 로 이은 것), 정답을 돌려준다."""
    params: dict[str, str] = {}
    for key, sql in (task.get("params_sql") or {}).items():
        if conn is None:
            raise ValueError(f"{task['id']}: params_sql 은 DB 가 있는 팩에서만 쓸 수 있습니다")
        row = conn.execute(sql).fetchone()
        params[key] = str(row[0]) if row else "?"
    filled = _fill({k: v for k, v in task.items() if k != "params_sql"}, params)
    if filled.get("turns") and not filled.get("question"):
        filled["question"] = " → ".join(filled["turns"])
    return filled, filled["question"], _truth(filled, conn)


def resolve(task: dict, conn: sqlite3.Connection | None) -> tuple[str, list[Any]]:
    """질문의 {변수} 를 채우고 정답을 만든다(prepare 의 호환용 얼굴). truth_sql(DB) · truth_expr(공식) · truth_value(상수) 순."""
    _, question, truth = prepare(task, conn)
    return question, truth


def _truth(task: dict, conn: sqlite3.Connection | None) -> list[Any]:
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
    return truth


def run_turns(agent: Agent, turns: list[str]) -> tuple[Any, list[dict[str, Any]]]:
    """여러 턴을 서비스와 같은 방식으로 돌린다: 앞 턴의 질문·답을 history 로 넘긴다(도구 메시지는 넘기지 않는다).
    채점은 마지막 턴의 답으로 하되 도구 호출·승인 요청은 모든 턴을 합친다."""
    history: list[dict[str, str]] = []
    results = []
    for q in turns:
        res = agent.run(q, history=list(history))
        results.append(res)
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": res.answer}]
    last = results[-1]
    if len(results) == 1:
        return last, []
    merged = last.model_copy(
        update={
            "steps": sum(r.steps for r in results),
            "latency_s": round(sum(r.latency_s for r in results), 2),
            "tool_calls": [c for r in results for c in r.tool_calls],
            "approvals": [a for r in results for a in r.approvals],
            "events": [e for r in results for e in r.events],
        }
    )
    usage = Usage()
    for r in results:
        usage.add(r.usage)
    merged.usage = usage
    turn_rows = [
        {"question": q, "answer": r.answer, "steps": r.steps, "latency_s": r.latency_s, "stopped": r.stopped_reason}
        for q, r in zip(turns, results)
    ]
    return merged, turn_rows


def _provider_slug(provider: str) -> str:
    """결과 파일 이름용 provider 표기. compat:주소|모델 은 모델 이름만(주소의 점이 확장자로 오해되는 것도 막는다), 나머지는 기호를 - 로."""
    kind, _, rest = provider.partition(":")
    if kind == "compat":
        _, _, model = rest.partition("|")
        rest = model.rsplit("/", 1)[-1] or "model"
    text = f"{kind}-{rest}" if rest else kind
    return text.replace("->", "-to-").replace(":", "-").replace("/", "-").replace("|", "-").replace(".", "_")


def run_eval(
    provider: str = "local",
    n: int = 1,
    tasks_path: Path | None = None,
    limit: int | None = None,
    tag: str | None = None,
    console=None,
    db_path: Path = DEFAULT_DB,
    prompt_version: str = "v1",
    pack: str | None = None,
) -> dict[str, Any]:
    pk = load_pack(pack)
    tasks = yaml.safe_load((tasks_path or pk.tasks_path).read_text(encoding="utf-8"))
    if limit:
        tasks = tasks[:limit]
    pack_db = db_path if pk.name == "enterprise" else pk.db_path
    conn = sqlite3.connect(f"file:{pack_db}?mode=ro", uri=True) if pack_db else None
    prov = make_provider(provider)
    registry = ToolRegistry(pk.build_tools(db_path=pack_db) if pack_db else pk.build_tools())
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_provider = _provider_slug(provider)
    suffix = f"{safe_provider}{('_' + pk.name) if pk.name != 'enterprise' else ''}{('_' + tag) if tag else ''}"
    trace_dir = RESULTS_DIR / "traces" / f"{stamp}_{suffix}"
    collector_box: dict[str, TraceCollector] = {}
    system_prompt = pk.system_prompts[prompt_version]
    on_event = lambda ev: collector_box["c"].on_event(ev) if "c" in collector_box else None
    agents: dict[str, Agent] = {}

    def agent_for(scope_spec: str | None) -> Agent:
        """태스크에 scope(사용자별 데이터 범위)가 있으면 서비스의 registry_for·system_prompt_for 와 같은 구성으로 따로 만든다."""
        key = scope_spec or ""
        if key not in agents:
            scope = pk.parse_scope(scope_spec) if scope_spec else None
            reg = registry if scope is None else ToolRegistry(pk.build_tools(db_path=pack_db, scope=scope))
            note = pk.scope_note(scope) if scope is not None else ""
            agents[key] = Agent(
                prov, reg, system_prompt + note, approval_handler=auto_deny, max_steps=12, on_event=on_event
            )
        return agents[key]

    rows: list[dict[str, Any]] = []
    for raw_task in tasks:
        task, question, truth = prepare(raw_task, conn)
        turns = task.get("turns") or [question]
        agent = agent_for(task.get("scope"))
        runs = []
        for i in range(n):
            if task.get("seed_before") and pk.seed is not None:
                pk.seed()  # 상태 의존 문제(실행 기록 등)는 매번 같은 시드 상태에서 시작한다
            collector_box["c"] = TraceCollector(
                f"{task['id']}_run{i + 1}", question, {"provider": provider, "task": task["id"], "type": task["type"]}
            )
            res, turn_rows = run_turns(agent, turns)
            collector_box["c"].finish(res)
            tpath = write_jsonl(collector_box["c"], trace_dir / f"{task['id']}_run{i + 1}.jsonl")
            maybe_export(collector_box["c"])
            sc = score(task, res, truth)
            runs.append(
                {
                    "run": i + 1,
                    **sc,
                    "answer": redact_text(res.answer)
                    if redact_enabled()
                    else res.answer,  # 채점은 위에서 원문으로 끝났다
                    "steps": res.steps,
                    "latency_s": res.latency_s,
                    "prompt_tokens": res.usage.prompt_tokens,
                    "completion_tokens": res.usage.completion_tokens,
                    "by_model": res.usage.by_model,
                    "routes": [ev.data["route"] for ev in res.events if ev.kind == "llm" and ev.data.get("route")],
                    **({"turns": turn_rows} if turn_rows else {}),
                    "stopped": res.stopped_reason,
                    "tool_calls": maybe_redact(res.tool_calls),
                    "trace": str(tpath.relative_to(RESULTS_DIR)),
                    "trace_summary": collector_box["c"].summary(),
                }
            )
            if console:
                mark = "[green]PASS[/green]" if sc["pass"] else "[red]FAIL[/red]"
                failed = [k for k, v in sc["checks"].items() if not v]
                console.print(
                    f"{mark} {task['id']:4} run{i + 1} {res.steps}스텝 {res.latency_s:6.1f}s  {('실패: ' + ','.join(failed)) if failed else ''}"
                )
        rows.append(
            {
                "id": task["id"],
                "type": task["type"],
                "question": question,
                **({"scope": task["scope"]} if task.get("scope") else {}),
                "pass_rate": sum(r["pass"] for r in runs) / n,
                "runs": runs,
            }
        )

    summary = _summarize(
        rows,
        provider=provider,
        model=getattr(prov, "model", "?"),
        n=n,
        tag=tag,
        prompt_version=prompt_version,
        pack=pk.name,
        tasks_file=(tasks_path or pk.tasks_path).stem,
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    base = RESULTS_DIR / f"{stamp}_{suffix}"
    # with_suffix 는 이름 속 점(127.0.0.1)을 확장자로 보고 잘라 버린다 → 문자열로 붙인다
    Path(str(base) + ".json").write_text(
        json.dumps({"summary": summary, "tasks": rows}, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    Path(str(base) + ".md").write_text(render_markdown(summary, rows), encoding="utf-8")
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
    provider = meta.get("provider", "local")
    cost = cost_usd(provider, total_prompt, total_completion)
    routing: dict[str, Any] = {}
    if provider.startswith("route"):
        # 라우팅: 모델별 토큰을 모아 모델별 단가로 비용을 더한다. 어떤 실행이 왜 올라갔는지도 센다
        by_model: dict[str, dict[str, float]] = {}
        for r in all_runs:
            for m, u in (r.get("by_model") or {}).items():
                slot = by_model.setdefault(m, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0})
                for k in slot:
                    slot[k] += u.get(k, 0)
        for m, u in by_model.items():
            u.update(cost_usd(m, int(u["prompt_tokens"]), int(u["completion_tokens"])))
        strong_spec = provider.partition(":")[2].partition("->")[2] or "openai:gpt-5-mini"
        strong_calls = by_model.get(strong_spec, {}).get("calls", 0)
        all_calls = sum(u["calls"] for u in by_model.values())
        reasons: dict[str, int] = {}
        for r in all_runs:
            routes = r.get("routes") or []
            if routes:  # 올라간 뒤의 호출은 같은 사유가 반복되므로 실행당 첫 사유만 센다
                why = routes[0]
                key = why if why.startswith("cheap_response") else why.split(":")[0]  # "tool_errors:3" → tool_errors
                reasons[key] = reasons.get(key, 0) + 1
        cost = {
            "cost_usd": round(sum(u.get("cost_usd") or 0 for u in by_model.values()), 5),
            "cost_usd_if_hosted": round(sum(u.get("cost_usd_if_hosted") or 0 for u in by_model.values()), 5),
        }
        routing = {
            "by_model": by_model,
            "strong_calls": strong_calls,
            "all_calls": all_calls,
            "strong_share": round(strong_calls / all_calls, 3) if all_calls else 0,
            "escalated_runs": sum(1 for r in all_runs if strong_spec in (r.get("by_model") or {})),
            "escalation_reasons": reasons,
        }
    return {
        **meta,
        "tasks": len(rows),
        "runs": len(all_runs),
        **cost,
        **({"routing": routing} if routing else {}),
        "pass_rate": round(sum(r["pass"] for r in all_runs) / max(1, len(all_runs)), 3),
        "by_type": {k: round(sum(v) / len(v), 3) for k, v in by_type.items()},
        "avg_steps": round(statistics.mean(r["steps"] for r in all_runs), 2) if all_runs else 0,
        "p50_latency_s": round(statistics.median(r["latency_s"] for r in all_runs), 1) if all_runs else 0,
        "p95_latency_s": round(sorted(r["latency_s"] for r in all_runs)[int(0.95 * (len(all_runs) - 1))], 1)
        if all_runs
        else 0,
        "tokens_per_run": round(statistics.mean(r["prompt_tokens"] + r["completion_tokens"] for r in all_runs))
        if all_runs
        else 0,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def render_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# 평가 결과 · {summary['provider']} ({summary['model']}) · n={summary['n']}"
        + (f" · 팩 {summary['pack']}" if summary.get("pack") not in (None, "enterprise") else ""),
        "",
        f"- 통과율 {summary['pass_rate']:.0%} ({summary['runs']}회 실행, 태스크 {summary['tasks']}개) · 유형별 "
        + ", ".join(f"{k} {v:.0%}" for k, v in summary["by_type"].items()),
        f"- 평균 스텝 {summary['avg_steps']} · 지연 p50 {summary['p50_latency_s']}s / p95 {summary['p95_latency_s']}s · 실행당 토큰 {summary['tokens_per_run']}"
        + (f" · 비용 ${summary['cost_usd']:.4f}" if summary.get("cost_usd") is not None else "")
        + (
            f" (호스팅이면 ${summary['cost_usd_if_hosted']:.4f})"
            if summary.get("cost_usd_if_hosted") not in (None, summary.get("cost_usd"))
            else ""
        )
        + (f" · 프롬프트 {summary['prompt_version']}" if summary.get("prompt_version") else ""),
    ]
    if summary.get("routing"):
        ro = summary["routing"]
        per_model = " · ".join(
            f"{m}: 호출 {u['calls']}, 토큰 {int(u['prompt_tokens'] + u['completion_tokens']):,}, ${u.get('cost_usd') or 0:.4f}"
            for m, u in ro["by_model"].items()
        )
        lines.append(
            f"- 라우팅: 강한 모델 호출 {ro['strong_calls']}/{ro['all_calls']} ({ro['strong_share']:.0%}), 올라간 실행 {ro['escalated_runs']}/{summary['runs']}"
            + (f", 사유 {ro['escalation_reasons']}" if ro["escalation_reasons"] else "")
            + f" · {per_model}"
        )
    lines += [
        "",
        "| id | 유형 | 통과율 | 스텝 | 지연(s) | 실패 항목 | 답변(앞 80자) |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in rows:
        r0 = t["runs"][-1]
        failed = ",".join(k for k, v in r0["checks"].items() if not v) or "-"
        ans = (r0["answer"] or "").replace("\n", " ").replace("|", "/")[:80]
        lines.append(
            f"| {t['id']} | {t['type']} | {t['pass_rate']:.0%} | {r0['steps']} | {r0['latency_s']} | {failed} | {ans} |"
        )
    return "\n".join(lines) + "\n"


def rescore(path: Path, tasks_path: Path | None = None) -> dict[str, Any]:
    """저장된 결과 JSON 을 현재 채점 로직으로 다시 채점해 같은 이름으로 덮어쓴다(모델 재호출 없음)."""
    from ..agent.types import AgentResult

    data = json.loads(path.read_text(encoding="utf-8"))
    if "tasks" not in data:  # 검색 평가·일치율 파일 등은 건너뛴다
        return {"skipped": path.name}
    tasks = {
        t["id"]: t
        for t in yaml.safe_load(
            (tasks_path or load_pack(data["summary"].get("pack")).tasks_path).read_text(encoding="utf-8")
        )
    }
    for t in data["tasks"]:
        task = tasks[t["id"]]
        for r in t["runs"]:
            fake = AgentResult(
                answer=r["answer"] or "",
                steps=r["steps"],
                stopped_reason=r["stopped"],
                tool_calls=r.get("tool_calls", []),
                approvals=[{"name": n} for n in r.get("approvals", [])],
            )
            r.update(score(task, fake, r.get("truth", [])))
        t["pass_rate"] = sum(r["pass"] for r in t["runs"]) / len(t["runs"])
    meta = {
        k: data["summary"][k]
        for k in ("provider", "model", "n", "tag", "prompt_version", "pack")
        if k in data["summary"]
    }
    old_judge = data["summary"].get("judge")
    data["summary"] = {**_summarize(data["tasks"], **meta), "rescored_at": datetime.now().isoformat(timespec="seconds")}
    judged = [r for t in data["tasks"] for r in t["runs"] if r.get("judge")]
    if judged:  # 판정자 결과는 보존하고 규칙 채점과의 일치율만 다시 계산
        data["summary"]["judge"] = {
            **(old_judge or {}),
            "judged": len(judged),
            "pass_rate": round(sum(r["judge"]["pass"] for r in judged) / len(judged), 3),
            "agree_with_rules": round(sum(r["judge"]["pass"] == r["pass"] for r in judged) / len(judged), 3),
        }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    path.with_suffix(".md").write_text(render_markdown(data["summary"], data["tasks"]), encoding="utf-8")
    return data["summary"]
