import { useCallback, useEffect, useState } from "react";
import { apiAt } from "../api";
import type { Run } from "../types";
import { useProgress } from "../hooks";
import QueueBoard from "./QueueBoard";

const STATUS_KO: Record<string, string> = { queued: "대기", running: "실행 중", done: "완료", failed: "실패", cancelled: "취소됨" };

/** "맡긴 작업" 탭: 오래 걸리는 질문을 큐에 두고 상태를 본다. 끝난 작업은 열어서 작업지로 본다. */
export default function JobsView({ base, agentName, sessionId, ensureSession, onOpen }: { base: string; agentName: string; sessionId: string | null; ensureSession: (b: string) => Promise<string>; onOpen: (runId: string) => void }) {
  const api = apiAt(base);
  const [jobs, setJobs] = useState<Run[]>([]);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async () => { try { setJobs(await api.jobs(50)); setErr(null); } catch (e) { setErr((e as Error).message); } }, [base]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);
  const submit = async () => { const question = q.trim(); if (!question) return; const sid = sessionId ?? (await ensureSession(base)); await api.submitJob(question, sid); setQ(""); await load(); };
  const cancel = async (id: string) => { try { await api.cancelJob(id); } catch { /* 이미 끝남 */ } await load(); };
  const active = jobs.filter((j) => ["queued", "running"].includes(j.status));
  const progress = useProgress(base, jobs.filter((j) => j.status === "running").map((j) => j.id));
  return (
    <main className="desk page">
      <div className="deskhead"><h1>{agentName} · 맡긴 작업</h1><p>집계처럼 오래 걸리는 질문을 맡겨 두면 순서대로 처리하고 결과를 남깁니다.</p></div>
      <div className="compose">
        <textarea rows={1} value={q} onChange={(e) => setQ(e.target.value)} placeholder="맡길 질문" aria-label="맡길 질문" onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }} />
        <button className="primary" onClick={submit} disabled={!q.trim()}>맡기기</button>
      </div>
      {err && <p className="error">{err}</p>}
      <section className="block">
        <h2>큐 보드 <span className="muted">{active.length ? `${active.length}건 진행` : "진행 중 없음"}</span></h2>
        {jobs.length === 0 ? <p className="empty">맡긴 작업이 없습니다. 위에 질문을 맡기면 여기에 막대로 나타납니다.</p>
          : <QueueBoard jobs={jobs} progress={progress} onOpen={onOpen} />}
        <p className="muted small">대기는 빈 막대(기다린 시간), 실행 중은 채워지는 막대와 진행률, 끝난 것은 소요 시간만큼의 막대입니다. 진행률은 도구가 점마다 보고합니다.</p>
      </section>
      <section className="block">
        <h2>전체 <span className="muted">{jobs.length}건</span></h2>
        {jobs.length === 0 ? <p className="empty">맡긴 작업이 없습니다.</p> : (
          <div className="tablewrap"><table className="data wide">
            <thead><tr><th>등록</th><th>질문</th><th>상태</th><th>소요</th><th></th></tr></thead>
            <tbody>{jobs.map((j) => <tr key={j.id}><td>{j.created_at.slice(5, 16).replace("T", " ")}</td><td className="wrap">{j.question}</td><td><span className={`st-${j.status}`}>{STATUS_KO[j.status] ?? j.status}</span>{j.status === "running" && progress[j.id] && <span className="muted small"> {progress[j.id].done}/{progress[j.id].total}{progress[j.id].label ? ` · ${progress[j.id].label}` : ""}</span>}</td>
              <td className="num">{j.latency_s != null ? `${j.latency_s}초` : ""}</td>
              <td>{["queued", "running"].includes(j.status) ? <button className="ghost small" onClick={() => cancel(j.id)}>취소</button> : <button className="ghost small" onClick={() => onOpen(j.id)}>작업지로 열기</button>}</td></tr>)}</tbody>
          </table></div>
        )}
      </section>
    </main>
  );
}
