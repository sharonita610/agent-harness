import { useEffect, useState } from "react";
import { api } from "../api";
import type { Approval, Run, RunEvent, Trace } from "../types";
import TraceView from "./TraceView";

export default function RunDetail({ runId, refreshKey, onClose }: { runId: string; refreshKey: number; onClose: () => void }) {
  const [run, setRun] = useState<(Run & { events: RunEvent[]; approvals: Approval[] }) | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [showEvents, setShowEvents] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      try { const [r, t] = await Promise.all([api.run(runId), api.trace(runId)]); if (alive) { setRun(r); setTrace(t); setErr(null); } }
      catch (e) { if (alive) setErr(String(e)); }
    })();
    return () => { alive = false; };
  }, [runId, refreshKey]);
  if (err) return <div className="card"><span className="err">{err}</span></div>;
  if (!run || !trace) return <div className="card muted">불러오는 중…</div>;
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>실행 상세 · {run.id} <span className="pill">{run.kind}</span><span className={`pill st-${run.status}`}>{run.status}</span></h2>
        <button className="small" onClick={onClose}>닫기</button>
      </div>
      <div style={{ margin: "8px 0" }}><span className="muted small">질문</span><div>{run.question}</div></div>
      <div style={{ margin: "8px 0" }}><span className="muted small">답변</span><div className="answer">{run.answer ?? run.error ?? "(아직 없음)"}</div></div>
      <div className="meta">{run.steps ?? "-"}스텝 · {run.latency_s ?? "-"}s · 토큰 {run.prompt_tokens ?? 0}+{run.completion_tokens ?? 0} · {run.stopped_reason ?? ""}{run.approvals.length ? ` · 승인 ${run.approvals.map((a) => `${a.tool}:${a.status}`).join(", ")}` : ""}</div>
      <h2 style={{ marginTop: 12 }}>트레이스 (이벤트 → 스팬)</h2>
      <TraceView trace={trace} />
      <button className="small" style={{ marginTop: 8 }} onClick={() => setShowEvents((v) => !v)}>{showEvents ? "이벤트 숨기기" : `이벤트 ${run.events.length}개 보기`}</button>
      {showEvents && (
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>#</th><th>시각</th><th>종류</th><th>내용</th></tr></thead>
          <tbody>{run.events.map((e) => <tr key={e.seq}><td className="muted">{e.seq}</td><td className="muted">{e.ts.slice(11, 23)}</td><td>{e.kind}</td><td><pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 11.5 }}>{JSON.stringify(e.data).slice(0, 300)}</pre></td></tr>)}</tbody>
        </table>
      )}
    </div>
  );
}
