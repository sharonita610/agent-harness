"""실행기: 에이전트(동기)를 스레드에서 돌리고 이벤트를 큐로 흘려 SSE 로 내보낸다. 승인은 브로커를 통해 사람을 기다린다."""
from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator, Callable

from ..agent.loop import Agent
from ..agent.types import AgentResult
from .sse import ping, sse

Sink = Callable[[dict[str, Any]], None]


def build_agent(state, provider, run_id: str, sink: Sink, user=None) -> Agent:
    broker = state.broker

    def approval_handler(tool, args) -> bool:
        aid = broker.request(run_id, tool.name, args.model_dump(), str(tool.tier))
        sink({"kind": "approval_pending", "step": None,
              "data": {"approval_id": aid, "tool": tool.name, "arguments": args.model_dump(), "tier": str(tool.tier), "timeout_s": broker.timeout_s}})
        return broker.wait(aid)

    return Agent(provider, state.registry_for(user), state.system_prompt_for(user), approval_handler=approval_handler,
                 max_steps=state.max_steps, on_event=lambda ev: sink(ev.model_dump()))


def execute_run(state, run_id: str, session_id: str, question: str, provider_spec: str | None,
                sink: Sink, should_cancel: Callable[[], bool] | None = None, user=None) -> AgentResult:
    """스레드(또는 워커)에서 동기 실행. 저장소에 결과를 남긴다."""
    store = state.store
    store.start_run(run_id)
    try:
        provider = state.provider_factory(provider_spec or state.default_provider)
        agent = build_agent(state, provider, run_id, sink, user=user)
        res = agent.run(question, history=store.history(session_id), should_cancel=should_cancel)
    except Exception as e:
        store.finish_run(run_id, "failed", error=f"{type(e).__name__}: {e}")
        raise
    store.add_message(session_id, "user", question)
    store.add_message(session_id, "assistant", res.answer)
    store.finish_run(run_id, "cancelled" if res.stopped_reason == "cancelled" else "done", result=res)
    _export_trace(store, run_id, question)
    return res


def _export_trace(store, run_id: str, question: str) -> None:
    """HARNESS_OTLP_ENDPOINT 가 있으면 저장된 이벤트로 스팬을 만들어 내보낸다(실패해도 실행 결과엔 영향 없음)."""
    import os
    if not os.getenv("HARNESS_OTLP_ENDPOINT"):
        return
    try:
        from ..observability.tracer import maybe_export, spans_from_events
        r = store.get_run(run_id) or {}
        maybe_export(spans_from_events(run_id, question, store.events(run_id), created_at=r.get("started_at") or r.get("created_at")))
    except Exception:
        pass


def make_sink(store, run_id: str, forward: Callable[[dict[str, Any]], None] | None = None) -> Sink:
    seq = {"n": 0}
    lock = threading.Lock()

    def sink(ev: dict[str, Any]) -> None:
        if ev["kind"] == "delta":  # 토큰 조각은 저장하지 않는다(이벤트 표 폭주 방지). 화면으로만 흘린다.
            if forward:
                forward({**ev, "seq": None})
            return
        with lock:
            seq["n"] += 1
            n = seq["n"]
        store.add_event(run_id, n, ev["kind"], ev.get("step"), ev.get("data", {}))
        if forward:
            forward({**ev, "seq": n})

    return sink


async def stream_chat(state, session_id: str, question: str, provider_spec: str | None, user=None) -> AsyncIterator[str]:
    """POST /chat 의 본문. 이벤트를 SSE 로 흘리고 마지막에 done 을 보낸다."""
    store = state.store
    run_id = store.create_run(session_id, "chat", question)
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()
    cancel = threading.Event()
    sink = make_sink(store, run_id, forward=lambda ev: loop.call_soon_threadsafe(q.put_nowait, ev))
    fut = loop.run_in_executor(None, execute_run, state, run_id, session_id, question, provider_spec, sink, cancel.is_set, user)
    _END = object()
    fut.add_done_callback(lambda f: q.put_nowait(_END))  # 스레드 종료를 큐로 알려 15초 ping 대기를 피한다
    yield sse("run", {"run_id": run_id, "session_id": session_id})
    try:
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ping()
                continue
            if ev is _END:
                break
            yield sse(ev["kind"], {"seq": ev.get("seq"), "step": ev.get("step"), **ev.get("data", {})})
        res = fut.result()
        yield sse("done", {"run_id": run_id, "answer": res.answer, "steps": res.steps, "stopped_reason": res.stopped_reason,
                           "latency_s": res.latency_s, "usage": res.usage.model_dump(), "approvals": res.approvals})
    except asyncio.CancelledError:  # 클라이언트가 끊음 → 에이전트에 취소 신호
        cancel.set()
        raise
    except Exception as e:
        yield sse("error", {"run_id": run_id, "error": f"{type(e).__name__}: {e}"})
