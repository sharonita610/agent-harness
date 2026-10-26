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
    return Agent(make_provider(provider), ToolRegistry(build_tools()), SYSTEM_PROMPT, approval_handler=handler,
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
        verbose: bool = True, json_out: Optional[Path] = typer.Option(None, help="결과를 JSON 으로 저장")):
    """질문 하나를 에이전트에게 던진다."""
    agent = _build_agent(provider, approve, max_steps, verbose)
    res = agent.run(question)
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


@app.command()
def mcp():
    """회사 데이터 비서 도구를 MCP 서버(stdio) 로 노출한다."""
    from .mcp_server.enterprise_server import main
    main()


if __name__ == "__main__":
    app()
