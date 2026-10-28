import { useCallback, useEffect, useState } from "react";
import { apiAt } from "../api";
import type { Approval, Run } from "../types";
import ApprovalSlip from "./ApprovalCard";

export interface TrayItem { base: string; agent: string; a: Approval }
const STATUS_KO: Record<string, string> = { queued: "대기", running: "실행 중", done: "완료", failed: "실패", cancelled: "취소됨" };

interface Props { base: string; tray: TrayItem[]; onDecided: () => void; canApprove: boolean; sessionId: string | null; ensureSession: (base: string) => Promise<string>; selected: string | null; onSelect: (id: string) => void; refreshKey: number }

/** 오른쪽 레일: 모든 비서의 결재함, 지금 비서에게 맡긴 작업, 최근 기록. */
export default function Rail({ base, tray, onDecided, canApprove, sessionId, ensureSession, selected, onSelect, refreshKey }: Props) {
  const api = apiAt(base);
  const [jobs, setJobs] = useState<Run[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [q, setQ] = useState("");
  const [kept, setKept] = useState<Record<string, TrayItem & { d: "approve" | "deny" }>>({});  // 방금 결재한 전표는 도장을 보여주려고 몇 초 더 둔다
  const load = useCallback(async () => {
    try { const [j, r] = await Promise.all([api.jobs(10), api.runs(20)]); setJobs(j.filter((x) => ["queued", "running"].includes(x.status))); setRuns(r); } catch { /* 서버 다운 */ }
  }, [base]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); const t = setInterval(load, 4000); return () => clearInterval(t); }, [load, refreshKey]);
  const submit = async () => { const question = q.trim(); if (!question) return; const sid = sessionId ?? (await ensureSession(base)); await api.submitJob(question, sid); setQ(""); await load(); };
  const cancel = async (id: string) => { try { await api.cancelJob(id); } catch { /* 이미 끝남 */ } await load(); };
  const decide = (item: TrayItem) => async (id: string, d: "approve" | "deny") => {
    await apiAt(item.base).decide(id, d);
    setKept((k) => ({ ...k, [id]: { ...item, d } }));
    setTimeout(() => setKept((k) => { const n = { ...k }; delete n[id]; return n; }), 6000);
    onDecided();
  };
  const shown: (TrayItem & { d?: "approve" | "deny" })[] = [...tray.filter((t) => !kept[t.a.id]), ...Object.values(kept)];
  return (
    <aside className="rail">
      <section>
        <h2>결재함 <span>{tray.length ? `${tray.length}건 대기` : shown.length ? "방금 처리" : "비어 있음"}</span></h2>
        {shown.length === 0 ? <p className="empty">기다리는 결재가 없습니다. 비서가 데이터를 바꾸려 하면 여기에 전표가 옵니다.</p>
          : shown.map((it) => <ApprovalSlip key={it.a.id} a={{ approval_id: it.a.id, tool: it.a.tool, arguments: it.a.args, tier: it.a.tier, run_id: it.a.run_id }} onDecide={decide(it)} decided={it.d ?? null} canApprove={canApprove} from={it.agent} />)}
      </section>
      <section>
        <h2>맡긴 작업 <span>{jobs.length ? `${jobs.length}건 진행` : ""}</span></h2>
        <div className="jobform">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="오래 걸리는 질문 맡기기" aria-label="맡길 질문" onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) submit(); }} />
          <button className="small" onClick={submit} disabled={!q.trim()}>맡기기</button>
        </div>
        {jobs.length === 0 && <p className="empty">진행 중인 작업이 없습니다. 끝난 작업은 아래 기록에 남습니다.</p>}
        <div className="list">
          {jobs.map((j) => (
            <button key={j.id} className={`item ${selected === j.id ? "selected" : ""}`} onClick={() => onSelect(j.id)}>
              <span className="q"><span>{j.question}</span></span>
              <span className="side"><b className={`st-${j.status}`}>{STATUS_KO[j.status] ?? j.status}</b><button className="ghost small" onClick={(e) => { e.stopPropagation(); cancel(j.id); }}>취소</button></span>
            </button>
          ))}
        </div>
      </section>
      <section>
        <h2>최근 기록 <span>{runs.length ? `${runs.length}건` : ""}</span></h2>
        {runs.length === 0 && <p className="empty">아직 기록이 없습니다.</p>}
        <div className="list">
          {runs.map((r) => (
            <button key={r.id} className={`item ${selected === r.id ? "selected" : ""}`} onClick={() => onSelect(r.id)} title={r.id}>
              <span className="q"><span>{r.question}</span></span>
              <span className="side"><b className={`st-${r.status}`}>{STATUS_KO[r.status] ?? r.status}{r.kind === "job" ? " · 맡긴 작업" : r.kind === "a2a" ? " · 위임받음" : ""}</b>{r.latency_s != null ? `${r.steps}단계 ${r.latency_s}초` : r.created_at.slice(5, 16).replace("T", " ")}</span>
            </button>
          ))}
        </div>
      </section>
    </aside>
  );
}
