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
             tasks: Optional[Path] = None, limit: Optional[int] = None, tag: Optional[str] = None):
    """평가 태스크를 돌리고 성적표를 저장한다."""
    from .evals.runner import run_eval
    run_eval(provider=provider, n=n, tasks_path=tasks, limit=limit, tag=tag, console=console)


@app.command("retrieval-eval")
def retrieval_eval():
    """문서 검색 품질 비교(bm25 / vector / hybrid). 임베딩 서버(8082)가 떠 있으면 세 모드 전부."""
    from .evals.retrieval_eval import run
    run(console=console)


@app.command()
def rescore(results: list[Path]):
    """저장된 평가 결과를 현재 채점 로직으로 다시 채점한다(모델 재호출 없음)."""
    from .evals.runner import rescore as _rescore
    for r in results:
        s = _rescore(r)
        console.print(f"{r.name}: 통과율 {s['pass_rate']:.0%}")


@app.command()
def mcp():
    """회사 데이터 비서 도구를 MCP 서버(stdio) 로 노출한다."""
    from .mcp_server.enterprise_server import main
    main()


if __name__ == "__main__":
    app()
