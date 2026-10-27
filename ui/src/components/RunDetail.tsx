import { useEffect, useState } from "react";
import { api } from "../api";
import type { Approval, Run, RunEvent, Trace } from "../types";
import TraceView from "./TraceView";
import Rich from "./Rich";

const STATUS_KO: Record<string, string> = { done: "완료", failed: "실패", cancelled: "취소됨", running: "실행 중", queued: "대기" };

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
  if (err) return <section className="detail"><p className="error">{err}</p></section>;
  if (!run || !trace) return <section className="detail"><p className="muted">불러오는 중</p></section>;
  return (
    <section className="detail">
      <header>
        <h2>실행 상세 <span className="muted" style={{ fontWeight: 400, fontSize: 14 }}>{run.id}</span></h2>
        <button className="ghost small" onClick={onClose}>닫기</button>
      </header>
      <p className="q"><span className="muted">질문 </span>{run.question}</p>
      <div className="answer"><Rich text={run.answer ?? run.error ?? "(아직 답이 없습니다)"} /></div>
      <div className="meta">
        <span className={`st-${run.status}`}>{STATUS_KO[run.status] ?? run.status}</span>
        <span>{run.steps ?? "-"}단계</span><span>{run.latency_s ?? "-"}초</span><span>토큰 {(run.prompt_tokens ?? 0) + (run.completion_tokens ?? 0)}</span>
        {run.approvals.length > 0 && <span>결재 {run.approvals.map((a) => `${a.tool} ${a.status === "approved" ? "승인" : a.status === "denied" ? "반려" : a.status}`).join(", ")}</span>}
      </div>
      <h3>시간이 어디에 쓰였나</h3>
      <TraceView trace={trace} />
      <button className="ghost small" style={{ marginTop: 10 }} onClick={() => setShowEvents((v) => !v)}>{showEvents ? "이벤트 원문 숨기기" : `이벤트 원문 ${run.events.length}개 보기`}</button>
      {showEvents && (
        <table className="events">
          <thead><tr><th>#</th><th>시각</th><th>종류</th><th>내용</th></tr></thead>
          <tbody>{run.events.map((e) => <tr key={e.seq}><td className="muted">{e.seq}</td><td className="muted">{e.ts.slice(11, 23)}</td><td>{e.kind}</td><td><pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{JSON.stringify(e.data).slice(0, 300)}</pre></td></tr>)}</tbody>
        </table>
      )}
    </section>
  );
}
