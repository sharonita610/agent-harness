"""터미널 진입점. uv run harness --help"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from .agent.types import AgentEvent

app = typer.Typer(help="도메인 교체형 에이전트 플랫폼 (1차 도메인: 회사 데이터 비서)", no_args_is_help=True)
console = Console()


def _printer(verbose: bool):
    def on_event(ev: AgentEvent) -> None:
        d = ev.data
        if ev.kind == "step" and verbose:
            console.rule(f"[dim]step {ev.step}")
        elif ev.kind == "delta":
            console.print(d.get("text", ""), end="", style="dim", highlight=False, markup=False)
        elif ev.kind == "tool_call":
            console.print(f"[cyan]→ {d['name']}[/cyan] {json.dumps(d.get('arguments'), ensure_ascii=False)[:300]}")
        elif ev.kind == "tool_result" and verbose:
            console.print(f"[dim]← {d['chars']}자: {d['preview'][:200]}[/dim]")
        elif ev.kind == "tool_error":
            console.print(f"[red]✗ {d['name']}: {d['error'][:200]}[/red]")
        elif ev.kind == "approval_request":
            console.print(f"[yellow]⚠ 승인 요청 {d['name']} {json.dumps(d['arguments'], ensure_ascii=False)}[/yellow]")
        elif ev.kind == "approval_result":
            console.print(f"[yellow]  → {'승인' if d['approved'] else '거부'}[/yellow]")
        elif ev.kind == "denied":
            console.print(f"[red]⛔ 차단 {d['name']} ({d['tier']})[/red]")
        elif ev.kind == "llm" and verbose and d.get("text") and d.get("tool_calls"):
            console.print(f"[dim]모델 메모: {d['text'][:200]}[/dim]")
        elif ev.kind == "stopped":
            console.print(f"[magenta]중단: {d.get('reason')} {d.get('error', '')}[/magenta]")
    return on_event


def _build_agent(provider: str, approve: str, max_steps: int, verbose: bool):
    from .agent.loop import Agent
    from .agent.providers import make_provider
    from .packs.enterprise.tools import SYSTEM_PROMPT, build_tools
    from .tools.permissions import auto_approve, auto_deny, cli_approval
    from .tools.registry import ToolRegistry

    handler = {"ask": cli_approval, "deny": auto_deny, "approve": auto_approve}[approve]
    prov = make_provider(provider)
    if provider.startswith(("local", "compat")):
        from .agent.providers import DEFAULT_LOCAL_BASE_URL, wait_ready
        import os
        base = os.getenv("LLM_BASE_URL", DEFAULT_LOCAL_BASE_URL) if provider.startswith("local") else provider.split(":", 1)[1].split("|")[0]
        if not wait_ready(base, on_wait=lambda: console.print("[dim]로컬 모델 서버 준비 대기 중… (scripts/serve_llm.sh 가 떠 있어야 합니다)[/dim]")):
            console.print(f"[red]서버 응답 없음: {base}. 다른 터미널에서 scripts/serve_llm.sh 를 먼저 실행하세요.[/red]")
            raise typer.Exit(1)
    return Agent(prov, ToolRegistry(build_tools()), SYSTEM_PROMPT, approval_handler=handler,
                 max_steps=max_steps, on_event=_printer(verbose))


@app.command()
def seed():
    """가짜 회사 DB(data/enterprise.db) 를 시드 고정으로 다시 만든다."""
    from .packs.enterprise.seed import build
    info = build()
    for k, v in info.items():
        console.print(f"{k}: {v}")


@app.command()
def tools():
    """등록된 도구와 권한 등급을 보여준다."""
    from .packs.enterprise.tools import build_tools
    from .tools.permissions import Policy, describe
    pol = Policy()
    for t in build_tools():
        console.print(f"[bold]{t.name:22}[/bold] {t.tier:12} {describe(pol.decide(t)):10} {t.description[:60]}")


@app.command()
def ask(question: str, provider: str = typer.Option("local", help="local | openai:모델 | anthropic:모델 | compat:URL|모델"),
        approve: str = typer.Option("ask", help="ask | deny | approve"), max_steps: int = 12,
        verbose: bool = True, json_out: Optional[Path] = typer.Option(None, help="결과를 JSON 으로 저장"),
        trace: bool = typer.Option(False, help="실행 트레이스를 traces/<run_id>.jsonl 로 저장 (HARNESS_OTLP_ENDPOINT 가 있으면 OTLP 로도 내보냄)")):
    """질문 하나를 에이전트에게 던진다."""
    agent = _build_agent(provider, approve, max_steps, verbose)
    collector = None
    if trace:
        from .observability.tracer import TraceCollector
        from .service.store import new_id
        collector = TraceCollector(new_id("run"), question, {"provider": provider})
        printer = agent.on_event
        agent.on_event = lambda ev: (collector.on_event(ev), printer(ev))
    res = agent.run(question)
    console.print()  # 스트리밍 줄 마감
    if collector:
        from .observability.tracer import maybe_export, write_jsonl
        collector.finish(res)
        path = write_jsonl(collector)
        exported = maybe_export(collector)
        s = collector.summary()
        console.print(f"[dim]트레이스: {path} · 스팬 {s['spans']} · LLM {s['llm_ms']}ms · 도구 {s['tool_ms']}ms" + (" · OTLP 전송됨" if exported else "") + "[/dim]")
    console.print(Panel(res.answer or "(빈 답변)", title=f"답변 · {res.steps}스텝 · {res.latency_s}s · 토큰 {res.usage.prompt_tokens}+{res.usage.completion_tokens} · {res.stopped_reason}"))
    if json_out:
        json_out.write_text(res.model_dump_json(indent=2, exclude={"messages"}), encoding="utf-8")
        console.print(f"[dim]저장: {json_out}[/dim]")


@app.command()
def chat(provider: str = "local", approve: str = "ask", max_steps: int = 12, verbose: bool = False):
    """대화형. 종료는 /q"""
    agent = _build_agent(provider, approve, max_steps, verbose)
    history: list[dict] = []
    console.print("[dim]회사 데이터 비서. /q 로 종료[/dim]")
    while True:
        try:
            q = console.input("[bold green]나> [/bold green]").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            continue
        if q in ("/q", "/quit", "/exit"):
            break
        res = agent.run(q, history=history)
        console.print(Panel(res.answer or "(빈 답변)", title=f"비서 · {res.steps}스텝 · {res.latency_s}s"))
        history += [{"role": "user", "content": q}, {"role": "assistant", "content": res.answer}]
        history = history[-10:]


@app.command("eval")
def eval_cmd(provider: str = "local", n: int = typer.Option(1, help="태스크당 반복 횟수"),
             tasks: Optional[Path] = None, limit: Optional[int] = None, tag: Optional[str] = None,
             prompt: str = typer.Option("v1", help="시스템 프롬프트 버전: v1 | v2")):
    """평가 태스크를 돌리고 성적표를 저장한다."""
    from .evals.runner import run_eval
    run_eval(provider=provider, n=n, tasks_path=tasks, limit=limit, tag=tag, console=console, prompt_version=prompt)


@app.command("retrieval-eval")
def retrieval_eval():
    """문서 검색 품질 비교(bm25 / vector / hybrid). 임베딩 서버(8082)가 떠 있으면 세 모드 전부."""
    from .evals.retrieval_eval import run
    run(console=console)


@app.command()
def judge(results: Path, provider: str = "local", limit: Optional[int] = None):
    """저장된 평가 결과를 LLM 판정자가 다시 채점해 judge 필드를 채운다(에이전트 재실행 없음)."""
    from .evals.judge import judge_results
    judge_results(results, provider_spec=provider, limit=limit, console=console)


@app.command("grade-sheet")
def grade_sheet(results: Path, n: int = 20):
    """사람 채점용 HTML 을 results/grading/ 에 만든다. 브라우저에서 채점하고 JSON 을 내보낸다."""
    from .evals.agreement import grading_page
    out = results.parent / "grading" / f"{results.stem}.html"
    grading_page(results, out, n=n)
    console.print(f"채점표: {out}\n열기: uv run python -m http.server 8765 --directory {results.parent}  → http://127.0.0.1:8765/grading/{out.name}")


@app.command()
def agreement(results: Path, human: Optional[Path] = typer.Argument(None, help="채점표에서 내보낸 human-*.json. 생략하면 results/ 와 ~/Downloads 에서 찾는다")):
    """규칙 채점 · 판정자 · 사람 채점의 일치율과 kappa."""
    import json as _json
    from .evals.agreement import compute_agreement
    name = f"human-{results.stem}.json"
    candidates = [human] if human else [results.parent / name, Path.home() / "Downloads" / name]
    found = next((p for p in candidates if p and p.exists()), None)
    if not found:
        console.print(f"[red]사람 채점 파일이 없습니다.[/red] 찾은 곳: {', '.join(str(p) for p in candidates)}\n"
                      f"채점표(results/grading/{results.stem}.html)에서 20개를 채점한 뒤 '채점 JSON 내보내기' 를 누르면 ~/Downloads/{name} 이 생깁니다. 그 뒤 이 명령을 다시 실행하세요.")
        raise typer.Exit(1)
    if found.parent != results.parent:  # Downloads 에 있으면 results 로 복사해 둔다
        (results.parent / name).write_bytes(found.read_bytes())
        console.print(f"[dim]{found} → {results.parent / name} 복사[/dim]")
    out = compute_agreement(_json.loads(results.read_text(encoding="utf-8")), _json.loads(found.read_text(encoding="utf-8")))
    if out["n"] == 0:
        console.print("[yellow]채점된 항목이 0개입니다. 채점표에서 통과/실패를 고른 뒤 다시 내보내세요.[/yellow]")
        raise typer.Exit(1)
    console.print(_json.dumps(out, ensure_ascii=False, indent=2))
    (results.parent / f"agreement_{results.stem}.json").write_text(_json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


@app.command()
def rescore(results: list[Path]):
    """저장된 평가 결과를 현재 채점 로직으로 다시 채점한다(모델 재호출 없음)."""
    from .evals.runner import rescore as _rescore
    for r in results:
        s = _rescore(r)
        console.print(f"{r.name}: " + (f"통과율 {s['pass_rate']:.0%}" if "pass_rate" in s else "건너뜀"))


users_app = typer.Typer(help="서비스 사용자(API 키)와 역할 관리. 키는 해시로만 저장되고 발급 때 한 번만 보인다.")
app.add_typer(users_app, name="users")


@users_app.command("add")
def users_add(name: str, role: str = typer.Option("operator", help="viewer | operator | approver | admin")):
    from .service.auth import UserStore
    us = UserStore.load()
    key = us.add(name, role)
    console.print(f"[bold]{name}[/bold] ({role}) 키: [green]{key}[/green]\n이 키는 다시 볼 수 없습니다. 파일: {us.path}")


@users_app.command("list")
def users_list():
    from .service.auth import UserStore
    us = UserStore.load()
    if not us.users:
        console.print("등록된 사용자 없음 → 서비스는 개발 모드(인증 없음)로 뜹니다. HARNESS_AUTH=1 로 강제할 수 있습니다.")
    for u in us.list():
        console.print(f"{u['name']:12} {u['role']}")


@users_app.command("remove")
def users_remove(name: str):
    from .service.auth import UserStore
    us = UserStore.load()
    console.print(f"{us.remove(name)}명 삭제")


@app.command()
def mcp():
    """회사 데이터 비서 도구를 MCP 서버(stdio) 로 노출한다."""
    from .mcp_server.enterprise_server import main
    main()


if __name__ == "__main__":
    app()
