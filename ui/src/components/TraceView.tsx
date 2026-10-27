import type { Span, Trace } from "../types";

export interface TraceRow { span: Span; depth: number; leftPct: number; widthPct: number }

/** 루트 스팬 기준으로 각 스팬의 가로 위치(%)를 계산한다. 부모 → 자식 순, 시작 시각 순. */
export function layoutSpans(spans: Span[]): TraceRow[] {
  const root = spans.find((s) => s.kind === "run") ?? spans[0];
  if (!root) return [];
  const t0 = root.start;
  const total = Math.max((root.end ?? root.start) - t0, 1e-6);
  const depthOf = new Map<string, number>();
  const byParent = new Map<string | null, Span[]>();
  for (const s of spans) byParent.set(s.parent_id, [...(byParent.get(s.parent_id) ?? []), s]);
  const out: TraceRow[] = [];
  const walk = (s: Span, depth: number) => {
    depthOf.set(s.span_id, depth);
    out.push({ span: s, depth, leftPct: Math.max(0, ((s.start - t0) / total) * 100), widthPct: Math.max(0.5, (((s.end ?? s.start) - s.start) / total) * 100) });
    for (const c of (byParent.get(s.span_id) ?? []).sort((a, b) => a.start - b.start)) walk(c, depth + 1);
  };
  walk(root, 0);
  return out;
}

export default function TraceView({ trace }: { trace: Trace }) {
  const rows = layoutSpans(trace.spans);
  const s = trace.summary;
  return (
    <div className="trace">
      <div className="legend">
        <span><i style={{ background: "var(--s-llm)" }} />모델 생각 {s.llm_calls}회, {(s.llm_ms / 1000).toFixed(1)}초</span>
        <span><i style={{ background: "var(--s-tool)" }} />도구 실행 {s.tool_calls}회, {(s.tool_ms / 1000).toFixed(1)}초{s.tool_errors ? ` (오류 ${s.tool_errors})` : ""}</span>
        <span><i style={{ background: "var(--s-approval)" }} />사람 결재 대기</span>
        <span>전체 {(s.total_ms / 1000).toFixed(1)}초</span>
      </div>
      {rows.map(({ span, depth, leftPct, widthPct }) => (
        <div className="r" key={span.span_id} title={`${span.name} · ${span.status} · ${JSON.stringify(span.attrs).slice(0, 300)}`}>
          <span className="name" style={{ paddingLeft: depth * 14 }}>{span.name}{span.status !== "ok" && <span className={`st-${span.status}`}> · {span.status}</span>}</span>
          <span className="track"><span className={`bar ${span.kind} ${span.status === "ok" ? "" : "bad"}`} style={{ left: `${leftPct}%`, width: `${widthPct}%` }} /></span>
          <span className="dur">{span.duration_ms != null ? (span.duration_ms >= 1000 ? `${(span.duration_ms / 1000).toFixed(1)}s` : `${Math.round(span.duration_ms)}ms`) : ""}</span>
        </div>
      ))}
    </div>
  );
}
