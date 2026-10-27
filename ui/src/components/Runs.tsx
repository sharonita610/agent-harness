import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Run } from "../types";

const STATUS_KO: Record<string, string> = { queued: "대기", running: "실행 중", done: "완료", failed: "실패", cancelled: "취소됨" };

export default function Runs({ refreshKey, selected, onSelect }: { refreshKey: number; selected: string | null; onSelect: (id: string) => void }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const load = useCallback(async () => { try { setRuns(await api.runs(25)); } catch { /* ignore */ } }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load, refreshKey]);
  return (
    <div className="list">
      {runs.length === 0 && <p className="empty">아직 실행 기록이 없습니다.</p>}
      {runs.map((r) => (
        <button key={r.id} className={`item ${selected === r.id ? "selected" : ""}`} onClick={() => onSelect(r.id)} title={r.id}>
          <span className="q">{r.question}</span>
          <span className="side"><b className={`st-${r.status}`}>{STATUS_KO[r.status] ?? r.status}{r.kind === "job" ? " (예약)" : ""}</b>{r.latency_s != null ? `${r.steps}단계 ${r.latency_s}초` : ""}</span>
        </button>
      ))}
    </div>
  );
}
