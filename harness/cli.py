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


PACK_HELP = "도메인 팩: enterprise | spice (기본은 HARNESS_PACK, 없으면 enterprise)"


def _build_agent(
    provider: str, approve: str, max_steps: int, verbose: bool, scope: Optional[str] = None, pack: Optional[str] = None
):
    from .agent.loop import Agent
    from .agent.providers import make_provider
    from .packs import load_pack
    from .tools.permissions import auto_approve, auto_deny, cli_approval
    from .tools.registry import ToolRegistry

    pk = load_pack(pack)
    handler = {"ask": cli_approval, "deny": auto_deny, "approve": auto_approve}[approve]
    sc = pk.parse_scope(scope)
    prov = make_provider(provider)
    if provider.startswith(("local", "compat")):
        import os

        from .agent.providers import DEFAULT_LOCAL_BASE_URL, wait_ready

        base = (
            os.getenv("LLM_BASE_URL", DEFAULT_LOCAL_BASE_URL)
            if provider.startswith("local")
            else provider.split(":", 1)[1].split("|")[0]
        )
        if not wait_ready(
            base,
            on_wait=lambda: console.print(
                "[dim]로컬 모델 서버 준비 대기 중… (scripts/serve_llm.sh 가 떠 있어야 합니다)[/dim]"
            ),
        ):
            console.print(
                f"[red]서버 응답 없음: {base}. 다른 터미널에서 scripts/serve_llm.sh 를 먼저 실행하세요.[/red]"
            )
            raise typer.Exit(1)
    if sc:
        console.print(f"[dim]데이터 범위: {sc.describe()}[/dim]")
    if pk.name != "enterprise":
        console.print(f"[dim]팩: {pk.title} ({pk.name})[/dim]")
    return Agent(
        prov,
        ToolRegistry(pk.build_tools(scope=sc)),
        pk.system_prompt + pk.scope_note(sc),
        approval_handler=handler,
        max_steps=max_steps,
        on_event=_printer(verbose),
    )


@app.command()
def seed(
    pack: Optional[str] = typer.Option(
        None, help="enterprise: 가짜 회사 DB · spice: 실험 추적 그래프 기준 실행(기존 기록 삭제)"
    ),
):
    """팩의 데이터를 시드 고정으로 다시 만든다. enterprise 는 data/enterprise.db, spice 는 실험 그래프."""
    from .packs import load_pack

    pk = load_pack(pack)
    if pk.seed is None:
        console.print(f"{pk.name}: 시드할 데이터가 없습니다.")
        raise typer.Exit(0)
    info = pk.seed()
    for k, v in info.items():
        console.print(f"{k}: {v}")


@app.command()
def tools(pack: Optional[str] = typer.Option(None, help=PACK_HELP)):
    """등록된 도구와 권한 등급을 보여준다."""
    from .packs import load_pack
    from .tools.permissions import Policy, describe

    pol = Policy()
    pk = load_pack(pack)
    console.print(f"[bold]{pk.title}[/bold] ({pk.name}) · {pk.description}")
    for t in pk.build_tools():
        console.print(f"[bold]{t.name:22}[/bold] {t.tier:12} {describe(pol.decide(t)):10} {t.description[:60]}")


@app.command()
def ask(
    question: str,
    provider: str = typer.Option("local", help="local | openai:모델 | anthropic:모델 | compat:URL|모델"),
    approve: str = typer.Option("ask", help="ask | deny | approve"),
    max_steps: int = 12,
    verbose: bool = True,
    json_out: Optional[Path] = typer.Option(None, help="결과를 JSON 으로 저장"),
    trace: bool = typer.Option(
        False, help="실행 트레이스를 traces/<run_id>.jsonl 로 저장 (HARNESS_OTLP_ENDPOINT 가 있으면 OTLP 로도 내보냄)"
    ),
    scope: Optional[str] = typer.Option(None, help="데이터 범위 시뮬레이션. 예: 'region=부산'"),
    pack: Optional[str] = typer.Option(None, help=PACK_HELP),
):
    """질문 하나를 에이전트에게 던진다."""
    agent = _build_agent(provider, approve, max_steps, verbose, scope, pack)
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
        console.print(
            f"[dim]트레이스: {path} · 스팬 {s['spans']} · LLM {s['llm_ms']}ms · 도구 {s['tool_ms']}ms"
            + (" · OTLP 전송됨" if exported else "")
            + "[/dim]"
        )
    console.print(
        Panel(
            res.answer or "(빈 답변)",
            title=f"답변 · {res.steps}스텝 · {res.latency_s}s · 토큰 {res.usage.prompt_tokens}+{res.usage.completion_tokens} · {res.stopped_reason}",
        )
    )
    if json_out:
        json_out.write_text(res.model_dump_json(indent=2, exclude={"messages"}), encoding="utf-8")
        console.print(f"[dim]저장: {json_out}[/dim]")


@app.command()
def chat(
    provider: str = "local",
    approve: str = "ask",
    max_steps: int = 12,
    verbose: bool = False,
    pack: Optional[str] = typer.Option(None, help=PACK_HELP),
):
    """대화형. 종료는 /q"""
    from .packs import load_pack

    agent = _build_agent(provider, approve, max_steps, verbose, None, pack)
    history: list[dict] = []
    console.print(f"[dim]{load_pack(pack).title}. /q 로 종료[/dim]")
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
def eval_cmd(
    provider: str = "local",
    n: int = typer.Option(1, help="태스크당 반복 횟수"),
    tasks: Optional[Path] = None,
    limit: Optional[int] = None,
    tag: Optional[str] = None,
    prompt: str = typer.Option("v1", help="시스템 프롬프트 버전: v1 | v2"),
    pack: Optional[str] = typer.Option(None, help=PACK_HELP),
):
    """평가 태스크를 돌리고 성적표를 저장한다."""
    from .evals.runner import run_eval

    run_eval(
        provider=provider,
        n=n,
        tasks_path=tasks,
        limit=limit,
        tag=tag,
        console=console,
        prompt_version=prompt,
        pack=pack,
    )


@app.command("retrieval-eval")
def retrieval_eval(
    pack: Optional[str] = typer.Option(None, help=PACK_HELP),
    index: str = typer.Option("auto", help="spice: auto | kb(매뉴얼 지식 베이스) | docs(요약 문서 6개)"),
):
    """문서 검색 품질 비교(bm25 / vector / hybrid). 임베딩 서버(8082)가 떠 있으면 세 모드 전부."""
    from .evals.retrieval_eval import run
    from .packs import load_pack

    run(console=console, pack=load_pack(pack).name, index_kind=index)


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
    console.print(
        f"채점표: {out}\n열기(API 서버가 떠 있을 때): http://127.0.0.1:8787/grading/{out.stem}  → 채점 후 '서버에 저장' 버튼"
    )


@app.command()
def agreement(
    results: Path,
    human: Optional[Path] = typer.Argument(
        None, help="채점표에서 내보낸 human-*.json. 생략하면 results/ 와 ~/Downloads 에서 찾는다"
    ),
):
    """규칙 채점 · 판정자 · 사람 채점의 일치율과 kappa."""
    import json as _json

    from .evals.agreement import compute_agreement

    name = f"human-{results.stem}.json"
    candidates = [human] if human else [results.parent / name, Path.home() / "Downloads" / name]
    found = next((p for p in candidates if p and p.exists()), None)
    if not found:
        console.print(
            f"[red]사람 채점 파일이 없습니다.[/red] 찾은 곳: {', '.join(str(p) for p in candidates)}\n"
            f"채점표(results/grading/{results.stem}.html)에서 20개를 채점한 뒤 '채점 JSON 내보내기' 를 누르면 ~/Downloads/{name} 이 생깁니다. 그 뒤 이 명령을 다시 실행하세요."
        )
        raise typer.Exit(1)
    if found.parent != results.parent:  # Downloads 에 있으면 results 로 복사해 둔다
        (results.parent / name).write_bytes(found.read_bytes())
        console.print(f"[dim]{found} → {results.parent / name} 복사[/dim]")
    out = compute_agreement(
        _json.loads(results.read_text(encoding="utf-8")), _json.loads(found.read_text(encoding="utf-8"))
    )
    if out["n"] == 0:
        console.print("[yellow]채점된 항목이 0개입니다. 채점표에서 통과/실패를 고른 뒤 다시 내보내세요.[/yellow]")
        raise typer.Exit(1)
    console.print(_json.dumps(out, ensure_ascii=False, indent=2))
    (results.parent / f"agreement_{results.stem}.json").write_text(
        _json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
def users_add(
    name: str,
    role: str = typer.Option("operator", help="viewer | operator | approver | admin"),
    scope: Optional[str] = typer.Option(
        None, help="데이터 범위. 예: 'region=부산' 또는 'customers=1,5,9' (둘 다면 ;로 연결)"
    ),
):
    from .packs.enterprise.scope import Scope
    from .service.auth import UserStore

    us = UserStore.load()
    sc = Scope.parse(scope)
    key = us.add(name, role, scope=sc.to_dict() if sc else None)
    if sc:
        console.print(f"데이터 범위: {sc.describe()}")
    console.print(
        f"[bold]{name}[/bold] ({role}) 키: [green]{key}[/green]\n이 키는 다시 볼 수 없습니다. 파일: {us.path}"
    )


@users_app.command("list")
def users_list():
    from .service.auth import UserStore

    us = UserStore.load()
    if not us.users:
        console.print(
            "등록된 사용자 없음 → 서비스는 개발 모드(인증 없음)로 뜹니다. HARNESS_AUTH=1 로 강제할 수 있습니다."
        )
    from .packs.enterprise.scope import Scope

    for u in us.list():
        sc = Scope.parse(u.get("scope"))
        console.print(f"{u['name']:12} {u['role']:10} {('범위: ' + sc.describe()) if sc else ''}")


@users_app.command("remove")
def users_remove(name: str):
    from .service.auth import UserStore

    us = UserStore.load()
    console.print(f"{us.remove(name)}명 삭제")


kb_app = typer.Typer(help="지식 베이스: PDF 매뉴얼·마크다운 → SQLite(BM25 + 벡터). 회로 팩의 search_manual 이 쓴다.")
app.add_typer(kb_app, name="kb")
DEFAULT_KB = Path(__file__).resolve().parents[1] / "data" / "kb" / "ngspice-manual.sqlite"


def _embedder_for(spec: str):
    """local(HARNESS_EMBED_* 기본값) | openai | 'base_url|model'"""
    from .retrieval import OpenAIEmbedder

    if spec == "local":
        return OpenAIEmbedder()
    if spec == "openai":
        return OpenAIEmbedder(base_url="https://api.openai.com/v1", model="text-embedding-3-small")
    base, _, model = spec.partition("|")
    return OpenAIEmbedder(base_url=base, model=model or None)


@kb_app.command("build")
def kb_build(
    pdf: Optional[Path] = typer.Option(None, help="매뉴얼 PDF 경로"),
    name: str = typer.Option("ngspice manual", help="문서 이름"),
    docs: Optional[Path] = typer.Option(None, help="함께 넣을 마크다운 폴더(기본: 회로 팩 요약 문서 6개)"),
    out: Path = typer.Option(DEFAULT_KB, help="SQLite 파일"),
):
    """PDF 를 절 단위로 잘라 지식 베이스를 만든다(임베딩은 kb embed 로 따로)."""
    from .packs.spice.tools import DOCS_DIR
    from .retrieval.kb import KnowledgeBase, ingest_markdown_dir, ingest_pdf

    kb = KnowledgeBase(out)
    if pdf:
        chunks = ingest_pdf(pdf, name)
        console.print(f"{pdf.name}: 절 {len({c.section_id for c in chunks})}개 → 청크 {len(chunks)}개")
        kb.build(chunks, kind="pdf", path=str(pdf))
    md = ingest_markdown_dir(docs or DOCS_DIR)
    kb.build(md, kind="markdown", path=str(docs or DOCS_DIR))
    console.print(f"마크다운 청크 {len(md)}개 · 저장: {out}")
    console.print(kb.stats())


@kb_app.command("embed")
def kb_embed(
    model: str = typer.Option("local", help="local | openai | 'base_url|model'"), kb: Path = typer.Option(DEFAULT_KB)
):
    """아직 임베딩이 없는 청크만 계산해 저장한다(증분)."""
    from .retrieval.kb import KnowledgeBase

    emb = _embedder_for(model)
    store = KnowledgeBase(kb)
    n = store.embed(emb.model, emb, on_progress=lambda d, t: console.print(f"[dim]{d}/{t}[/dim]", end="\r"))
    console.print(f"\n{emb.model}: 새로 계산 {n}개 · 커버리지 {store.coverage(emb.model):.0%}")


@kb_app.command("search")
def kb_search(
    query: str,
    mode: str = typer.Option("auto", help="auto | bm25 | vector | hybrid"),
    k: int = 5,
    model: str = typer.Option("local", help="벡터 검색에 쓸 임베더: local | openai"),
    kb: Path = typer.Option(DEFAULT_KB),
):
    """지식 베이스를 검색해 본다."""
    from .retrieval.kb import KnowledgeBase

    emb = _embedder_for(model) if mode != "bm25" else None
    store = KnowledgeBase(kb, embedder=emb, embed_model=emb.model if emb else None)
    for h in store.search(query, k=k, mode=None if mode == "auto" else mode):
        console.print(f"[bold]{h['source']}[/bold]  [dim]{h['mode']} {h['score']}[/dim]\n  {h['snippet'][:160]}")


@kb_app.command("stats")
def kb_stats(kb: Path = typer.Option(DEFAULT_KB)):
    from .retrieval.kb import KnowledgeBase

    console.print(KnowledgeBase(kb).stats())


graph_app = typer.Typer(help="실험 추적 그래프(회로 팩): 실행 기록을 보거나 그림으로 내보낸다.")
app.add_typer(graph_app, name="graph")


@graph_app.command("stats")
def graph_stats():
    from .packs.spice.graph import ExperimentGraph

    console.print(ExperimentGraph().stats())


@graph_app.command("runs")
def graph_runs(
    component: Optional[str] = None, value: Optional[float] = None, measurement: Optional[str] = None, limit: int = 10
):
    """조건에 맞는 실행 기록."""
    from .packs.spice.graph import ExperimentGraph

    for r in ExperimentGraph().query_runs(component=component, value=value, measurement=measurement, limit=limit):
        console.print(f"[bold]{r['run_id']}[/bold] {r['circuit']} · {r['components']} · {r['measurements']}")


@graph_app.command("svg")
def graph_svg(out: Path = typer.Option(Path("docs/img/experiment-graph.svg")), max_runs: int = 10):
    """그래프를 SVG 로 그린다(회로 → 실행 → 측정)."""
    from .packs.spice.graph import ExperimentGraph, render_graph_svg

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_graph_svg(ExperimentGraph(), max_runs=max_runs), encoding="utf-8")
    console.print(f"저장: {out}")


@app.command()
def mcp(pack: Optional[str] = typer.Option(None, help=PACK_HELP)):
    """회사 데이터 비서 도구를 MCP 서버(stdio) 로 노출한다."""
    from .mcp_server.enterprise_server import main

    main(pack)


if __name__ == "__main__":
    app()
