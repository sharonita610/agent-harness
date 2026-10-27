import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Run } from "../types";

export default function Runs({ refreshKey, selected, onSelect }: { refreshKey: number; selected: string | null; onSelect: (id: string) => void }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const load = useCallback(async () => { try { setRuns(await api.runs(25)); } catch { /* ignore */ } }, []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load, refreshKey]);
  return (
    <table>
      <thead><tr><th>run</th><th>종류</th><th>상태</th><th>질문</th><th>스텝</th><th>시간</th></tr></thead>
      <tbody>
        {runs.map((r) => (
          <tr key={r.id} className={`clickable ${selected === r.id ? "selected" : ""}`} onClick={() => onSelect(r.id)}>
            <td className="muted">{r.id.slice(4, 12)}</td><td className="muted">{r.kind}</td><td className={`st-${r.status}`}>{r.status}</td>
            <td>{r.question.slice(0, 40)}</td><td>{r.steps ?? ""}</td><td className="muted">{r.latency_s != null ? `${r.latency_s}s` : ""}</td>
          </tr>
        ))}
        {runs.length === 0 && <tr><td colSpan={6} className="muted">실행 기록이 없습니다.</td></tr>}
      </tbody>
    </table>
  );
}
