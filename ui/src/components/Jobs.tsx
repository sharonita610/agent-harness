import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Run } from "../types";

interface Props { sessionId: string | null; ensureSession: () => Promise<string>; selected: string | null; onSelect: (id: string) => void }
const STATUS_KO: Record<string, string> = { queued: "대기", running: "실행 중", done: "완료", failed: "실패", cancelled: "취소됨" };

export default function Jobs({ sessionId, ensureSession, selected, onSelect }: Props) {
  const [q, setQ] = useState("");
  const [jobs, setJobs] = useState<Run[]>([]);
  const load = useCallback(async () => { try { setJobs(await api.jobs(15)); } catch { /* ignore */ } }, []);
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);
  const submit = async () => { const question = q.trim(); if (!question) return; const sid = sessionId ?? (await ensureSession()); await api.submitJob(question, sid); setQ(""); await load(); };
  const cancel = async (id: string) => { try { await api.cancelJob(id); } catch { /* 이미 끝남 */ } await load(); };
  return (
    <div>
      <div className="jobform">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="오래 걸리는 질문을 맡겨 두기" aria-label="예약 작업 질문" onKeyDown={(e) => { if (e.key === "Enter") submit(); }} />
        <button className="primary" onClick={submit} disabled={!q.trim()}>맡기기</button>
      </div>
      <div className="list">
        {jobs.length === 0 && <p className="empty">맡겨 둔 작업이 없습니다. 집계처럼 오래 걸리는 질문을 여기에 두면 순서대로 처리하고 결과를 남깁니다.</p>}
        {jobs.map((j) => (
          <button key={j.id} className={`item ${selected === j.id ? "selected" : ""}`} onClick={() => onSelect(j.id)}>
            <span className="q">{j.question}</span>
            <span className="side"><b className={`st-${j.status}`}>{STATUS_KO[j.status] ?? j.status}</b>{j.latency_s != null ? `${j.latency_s}초` : ""}</span>
            {["queued", "running"].includes(j.status) && <span><button className="ghost small" onClick={(e) => { e.stopPropagation(); cancel(j.id); }}>취소</button></span>}
          </button>
        ))}
      </div>
    </div>
  );
}
