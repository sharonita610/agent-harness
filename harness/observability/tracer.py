"""실행 트레이스: 에이전트 이벤트 → 스팬(run / llm / tool / approval).

- 루프 코드는 손대지 않고 on_event 를 가로채 스팬을 만든다(CLI·평가·서비스가 같은 수집기를 쓴다).
- JSONL 로 저장하고, OTLP 엔드포인트(HARNESS_OTLP_ENDPOINT)가 있으면 OpenTelemetry 로도 내보낸다(Jaeger·Langfuse·Phoenix 등).
- 서비스에 저장된 이벤트(ts 포함)로도 같은 스팬을 재구성할 수 있다(spans_from_events).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent.types import AgentEvent, AgentResult

ROOT = Path(__file__).resolve().parents[2]
TRACES_DIR = ROOT / "traces"


def _sid() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class Span:
    span_id: str
    parent_id: str | None
    name: str
    kind: str              # run | llm | tool | approval | nudge
    start: float           # epoch seconds
    end: float | None = None
    status: str = "ok"     # ok | error | denied | not_approved | cancelled | ...
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"span_id": self.span_id, "parent_id": self.parent_id, "name": self.name, "kind": self.kind,
                "start": self.start, "end": self.end, "duration_ms": round((self.end - self.start) * 1000, 1) if self.end else None,
                "status": self.status, "attrs": self.attrs}


class TraceCollector:
    """이벤트를 순서대로 먹여 스팬 트리를 만든다. ts 를 주지 않으면 현재 시각."""

    def __init__(self, run_id: str, question: str, meta: dict[str, Any] | None = None, start: float | None = None):
        self.run_id = run_id
        self.root = Span(_sid(), None, "agent.run", "run", start or time.time(), attrs={"run_id": run_id, "question": question[:500], **(meta or {})})
        self.spans: list[Span] = [self.root]
        self._open_tools: list[Span] = []
        self._open_approval: Span | None = None
        self._step_start: float | None = None
        self._closed = False

    # ------------------------------------------------------------------
    def on_event(self, ev: AgentEvent | dict[str, Any], ts: float | None = None) -> None:
        now = ts if ts is not None else time.time()
        kind = ev.kind if isinstance(ev, AgentEvent) else ev["kind"]
        step = ev.step if isinstance(ev, AgentEvent) else ev.get("step")
        d = ev.data if isinstance(ev, AgentEvent) else ev.get("data", {})
        if kind == "step":
            self._step_start = now
        elif kind == "llm":
            lat = d.get("latency_s")
            start = now - lat if lat else (self._step_start or now)
            self.spans.append(Span(_sid(), self.root.span_id, f"llm.step{step}", "llm", start, now,
                                   attrs={"step": step, "prompt_tokens": d.get("prompt_tokens"), "completion_tokens": d.get("completion_tokens"),
                                          "finish": d.get("finish"), "n_tool_calls": len(d.get("tool_calls") or []), "text_len": len(d.get("text") or "")}))
        elif kind == "nudge":
            self.spans.append(Span(_sid(), self.root.span_id, "loop.nudge", "nudge", now, now, attrs={"step": step, "reason": d.get("reason")}))
        elif kind == "tool_call":
            sp = Span(_sid(), self.root.span_id, f"tool.{d.get('name')}", "tool", now,
                      attrs={"step": step, "tool": d.get("name"), "arguments": d.get("arguments"), "call_id": d.get("id")})
            self.spans.append(sp)
            self._open_tools.append(sp)
        elif kind in ("tool_result", "tool_error", "denied"):
            sp = self._pop_tool(d.get("name"))
            if sp:
                sp.end = now
                if kind == "tool_result":
                    sp.attrs["result_chars"] = d.get("chars")
                elif kind == "tool_error":
                    sp.status = "error"
                    sp.attrs["error"] = (d.get("error") or "")[:300]
                else:
                    sp.status = "denied"
                    sp.attrs["tier"] = d.get("tier")
        elif kind == "approval_request":
            parent = self._open_tools[-1].span_id if self._open_tools else self.root.span_id
            self._open_approval = Span(_sid(), parent, f"approval.{d.get('name')}", "approval", now,
                                       attrs={"step": step, "tool": d.get("name"), "arguments": d.get("arguments"), "tier": d.get("tier")})
            self.spans.append(self._open_approval)
        elif kind == "approval_result":
            if self._open_approval:
                self._open_approval.end = now
                self._open_approval.status = "ok" if d.get("approved") else "not_approved"
                self._open_approval.attrs["approved"] = d.get("approved")
                self._open_approval = None
                if not d.get("approved"):
                    sp = self._pop_tool(d.get("name"))
                    if sp:
                        sp.end = now
                        sp.status = "not_approved"
        elif kind == "final":
            self._close_root(now, "final", answer_len=len(d.get("answer") or ""))
        elif kind == "stopped":
            self._close_root(now, d.get("reason") or "stopped", error=d.get("error"))

    def _pop_tool(self, name: str | None) -> Span | None:
        for i in range(len(self._open_tools) - 1, -1, -1):
            if name is None or self._open_tools[i].attrs.get("tool") == name:
                return self._open_tools.pop(i)
        return self._open_tools.pop() if self._open_tools else None

    def _close_root(self, now: float, reason: str, **attrs: Any) -> None:
        if self._closed:
            return
        for sp in self._open_tools:
            sp.end = now
            sp.status = "unfinished"
        self._open_tools.clear()
        self.root.end = now
        self.root.status = "ok" if reason == "final" else reason
        self.root.attrs.update({"stopped_reason": reason, **{k: v for k, v in attrs.items() if v is not None}})
        self._closed = True

    def finish(self, result: AgentResult | None = None, ts: float | None = None) -> None:
        now = ts if ts is not None else time.time()
        if not self._closed:
            self._close_root(now, result.stopped_reason if result else "unknown")
        if result:
            self.root.attrs.update({"steps": result.steps, "prompt_tokens": result.usage.prompt_tokens, "completion_tokens": result.usage.completion_tokens,
                                    "latency_s": result.latency_s, "n_tool_calls": len(result.tool_calls), "n_approvals": len(result.approvals)})

    def to_dicts(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.spans]

    def summary(self) -> dict[str, Any]:
        tools = [s for s in self.spans if s.kind == "tool"]
        llms = [s for s in self.spans if s.kind == "llm"]
        return {"run_id": self.run_id, "spans": len(self.spans), "llm_calls": len(llms), "tool_calls": len(tools),
                "tool_errors": sum(1 for s in tools if s.status == "error"),
                "llm_ms": round(sum((s.end or s.start) - s.start for s in llms) * 1000), "tool_ms": round(sum((s.end or s.start) - s.start for s in tools) * 1000),
                "total_ms": round(((self.root.end or time.time()) - self.root.start) * 1000)}


# ----------------------------------------------------------------------------- 저장/내보내기
def write_jsonl(collector: TraceCollector, path: Path | None = None) -> Path:
    path = path or TRACES_DIR / f"{collector.run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "summary", **collector.summary()}, ensure_ascii=False) + "\n")
        for s in collector.to_dicts():
            f.write(json.dumps({"type": "span", **s}, ensure_ascii=False, default=str) + "\n")
    return path


def spans_from_events(run_id: str, question: str, events: list[dict[str, Any]], created_at: str | None = None) -> TraceCollector:
    """서비스 저장소의 이벤트(ts 포함)로 스팬을 재구성한다."""
    start = datetime.fromisoformat(created_at).timestamp() if created_at else None
    first_ts = datetime.fromisoformat(events[0]["ts"]).timestamp() if events else None
    c = TraceCollector(run_id, question, start=start or first_ts)
    for e in events:
        c.on_event({"kind": e["kind"], "step": e.get("step"), "data": e.get("data", {})}, ts=datetime.fromisoformat(e["ts"]).timestamp())
    c.finish()
    return c


class OTelExporter:
    """OpenTelemetry 로 스팬 내보내기. endpoint 예: http://127.0.0.1:4318/v1/traces (Jaeger, Langfuse, Phoenix 등)."""

    def __init__(self, endpoint: str | None = None, service_name: str = "agent-harness"):
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        self.endpoint = endpoint or os.getenv("HARNESS_OTLP_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
        self._provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        self._provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=self.endpoint)))
        self._tracer = self._provider.get_tracer("harness")
        self._trace_api = trace

    def export(self, collector: TraceCollector) -> None:
        from opentelemetry.trace import StatusCode

        ctx_by_id: dict[str, Any] = {}
        order = sorted(collector.spans, key=lambda s: (s.parent_id is not None, s.start))
        for s in order:
            parent_ctx = ctx_by_id.get(s.parent_id) if s.parent_id else None
            span = self._tracer.start_span(s.name, context=parent_ctx, start_time=int(s.start * 1e9))
            for k, v in s.attrs.items():
                if v is None:
                    continue
                span.set_attribute(f"harness.{k}", v if isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False, default=str)[:2000])
            span.set_attribute("harness.kind", s.kind)
            if s.status not in ("ok",):
                span.set_status(StatusCode.ERROR if s.status in ("error", "unfinished") else StatusCode.UNSET, s.status)
            ctx_by_id[s.span_id] = self._trace_api.set_span_in_context(span)
            span.end(end_time=int((s.end or s.start) * 1e9))
        self._provider.force_flush(timeout_millis=5000)


_exporter: OTelExporter | None = None


def maybe_export(collector: TraceCollector) -> bool:
    """HARNESS_OTLP_ENDPOINT 가 설정돼 있으면 내보낸다."""
    global _exporter
    if not os.getenv("HARNESS_OTLP_ENDPOINT"):
        return False
    if _exporter is None:
        _exporter = OTelExporter()
    _exporter.export(collector)
    return True
