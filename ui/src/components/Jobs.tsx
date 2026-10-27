import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Run } from "../types";

interface Props { sessionId: string | null; ensureSession: () => Promise<string>; selected: string | null; onSelect: (id: string) => void }

export default function Jobs({ sessionId, ensureSession, selected, onSelect }: Props) {
  const [q, setQ] = useState("");
  const [jobs, setJobs] = useState<Run[]>([]);
  const load = useCallback(async () => { try { setJobs(await api.jobs(15)); } catch { /* ignore */ } }, []);
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);
  const submit = async () => {
    const question = q.trim(); if (!question) return;
    const sid = sessionId ?? (await ensureSession());
    await api.submitJob(question, sid); setQ(""); await load();
  };
  const cancel = async (id: string) => { try { await api.cancelJob(id); } catch { /* 이미 끝남 */ } await load(); };
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="오래 걸리는 질문 (예: 거래처 등급별 지난달 주문 금액과 지연 비율 표)" onKeyDown={(e) => { if (e.key === "Enter") submit(); }} />
        <button className="primary" onClick={submit}>잡 제출</button>
      </div>
      <table>
        <thead><tr><th>run</th><th>상태</th><th>질문</th><th></th></tr></thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id} className={`clickable ${selected === j.id ? "selected" : ""}`} onClick={() => onSelect(j.id)}>
              <td className="muted">{j.id.slice(4, 12)}</td>
              <td className={`st-${j.status}`}>{j.status}</td>
              <td>{j.question.slice(0, 48)}{j.answer ? <div className="muted small">{j.answer.slice(0, 80)}</div> : null}</td>
              <td>{["queued", "running"].includes(j.status) && <button className="small" onClick={(e) => { e.stopPropagation(); cancel(j.id); }}>취소</button>}</td>
            </tr>
          ))}
          {jobs.length === 0 && <tr><td colSpan={4} className="muted">아직 잡이 없습니다.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
