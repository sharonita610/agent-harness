"""LangGraph 비교 실험: 같은 도구(우리 ToolRegistry)·같은 로컬 모델·같은 평가 문제를 LangGraph 의 표준 ReAct 그래프로 돌려 본다.

목적은 "프레임워크를 쓸 줄 아는가" 와 "직접 쓴 루프에 무엇이 더 들어 있는가" 를 같은 조건에서 비교하는 것.
사용: uv run --with "langgraph>=0.2" --with "langchain-openai>=0.2" python docs/experiments/langgraph/run.py [--limit 6] [--pack enterprise]
      (scripts/serve_llm.sh 가 8080 에 떠 있어야 한다)
결과: docs/experiments/langgraph/results.md
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402
from langchain_core.tools import StructuredTool  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from langgraph.graph import START, MessagesState, StateGraph  # noqa: E402
from langgraph.prebuilt import ToolNode, tools_condition  # noqa: E402

from harness.agent.types import AgentResult  # noqa: E402
from harness.evals.runner import resolve, score  # noqa: E402
from harness.packs import load_pack  # noqa: E402
from harness.tools.permissions import Decision, Policy  # noqa: E402


def to_langchain_tools(tools, policy: Policy, approvals: list[dict]):
    """우리 Tool → LangChain StructuredTool. write 등급은 '승인 요청만 기록하고 실행하지 않음'(평가의 auto_deny 와 같은 조건), destructive 는 제외."""
    out = []
    for t in tools:
        decision = policy.decide(t)
        if decision == Decision.DENY:
            continue

        def make(tool=t, decision=decision):
            def fn(**kwargs):
                if decision == Decision.REQUIRE_APPROVAL:
                    approvals.append({"name": tool.name, "arguments": kwargs})
                    return "승인되지 않음: 사용자가 이 변경을 승인하지 않았습니다. 변경을 실행하지 말고, 승인이 필요하다는 사실과 대안을 안내하세요."
                try:
                    return json.dumps(tool.run(kwargs), ensure_ascii=False, default=str)[:4000]
                except Exception as e:  # 모델이 고칠 수 있게 문장으로
                    return f"도구 실행 오류({type(e).__name__}): {e}"

            return fn

        out.append(
            StructuredTool.from_function(func=make(), name=t.name, description=t.description, args_schema=t.params)
        )
    return out


def build_graph(llm, lc_tools):
    model = llm.bind_tools(lc_tools)

    def agent(state: MessagesState):
        return {"messages": [model.invoke(state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(lc_tools))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)  # tool_calls 가 있으면 tools, 없으면 END
    g.add_edge("tools", "agent")
    return g.compile()


def to_result(messages, approvals, elapsed) -> AgentResult:
    calls = []
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                calls.append({"name": tc["name"], "arguments": tc.get("args", {}), "status": "ok"})
    for c, tm in zip(calls, tool_msgs):
        content = tm.content if isinstance(tm.content, str) else str(tm.content)
        if content.startswith("도구 실행 오류") or getattr(tm, "status", "success") == "error":
            c["status"] = "exec_error"
        elif content.startswith("승인되지 않음"):
            c["status"] = "not_approved"
    steps = sum(1 for m in messages if isinstance(m, AIMessage))
    answer = next((m.content for m in reversed(messages) if isinstance(m, AIMessage) and not m.tool_calls), "") or ""
    if not isinstance(answer, str):
        answer = str(answer)
    return AgentResult(
        answer=answer,
        steps=steps,
        stopped_reason="final",
        tool_calls=calls,
        approvals=approvals,
        latency_s=round(elapsed, 1),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="enterprise")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    args = ap.parse_args()
    pk = load_pack(args.pack)
    tasks = yaml.safe_load(pk.tasks_path.read_text(encoding="utf-8"))[: args.limit]
    conn = sqlite3.connect(f"file:{pk.db_path}?mode=ro", uri=True) if pk.db_path else None
    tools = pk.build_tools(db_path=pk.db_path) if pk.db_path else pk.build_tools()
    policy = Policy()
    llm = ChatOpenAI(
        base_url=args.base_url, api_key="llama.cpp", model="local-llm", temperature=0.2, max_tokens=2048, timeout=300
    )
    rows = []
    for task in tasks:
        approvals: list[dict] = []
        lc_tools = to_langchain_tools(tools, policy, approvals)
        graph = build_graph(llm, lc_tools)
        question, truth = resolve(task, conn)
        t0 = time.perf_counter()
        try:
            state = graph.invoke(
                {"messages": [SystemMessage(content=pk.system_prompt), HumanMessage(content=question)]},
                config={"recursion_limit": 30},
            )
            res = to_result(state["messages"], approvals, time.perf_counter() - t0)
            err = ""
        except Exception as e:
            res = AgentResult(
                answer="",
                steps=0,
                stopped_reason="provider_error",
                latency_s=round(time.perf_counter() - t0, 1),
                approvals=approvals,
            )
            err = f"{type(e).__name__}: {str(e)[:120]}"
        sc = score(task, res, truth)
        failed = [k for k, v in sc["checks"].items() if not v]
        rows.append(
            {
                "id": task["id"],
                "type": task["type"],
                "pass": sc["pass"],
                "steps": res.steps,
                "latency_s": res.latency_s,
                "tools": [c["name"] for c in res.tool_calls],
                "failed": failed,
                "error": err,
                "answer": (res.answer or "")[:120].replace("\n", " "),
            }
        )
        print(
            f"{'PASS' if sc['pass'] else 'FAIL'} {task['id']:4} {res.steps}스텝 {res.latency_s:6.1f}s {failed or ''} {err}"
        )
    out = Path(__file__).parent / "results.md"
    n_pass = sum(r["pass"] for r in rows)
    lines = [
        f"# LangGraph 비교 실험 · {args.pack} · 처음 {len(rows)}문제 · 로컬 gpt-oss-20b",
        "",
        f"통과 {n_pass}/{len(rows)} · 평균 스텝 {sum(r['steps'] for r in rows) / len(rows):.2f} · 평균 지연 {sum(r['latency_s'] for r in rows) / len(rows):.1f}s",
        "",
        "| id | 유형 | 결과 | 스텝 | 지연 | 도구 | 실패 항목 / 오류 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['type']} | {'✅' if r['pass'] else '❌'} | {r['steps']} | {r['latency_s']}s | {' → '.join(r['tools'])} | {', '.join(r['failed'])} {r['error']} |"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
