import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Approval } from "../types";
import ApprovalCard from "./ApprovalCard";

export default function Approvals({ onDecided }: { onDecided: () => void }) {
  const [list, setList] = useState<Approval[]>([]);
  const [history, setHistory] = useState<Approval[]>([]);
  const load = useCallback(async () => {
    try { setList(await api.approvals("pending")); setHistory((await api.approvals("all")).filter((a) => a.status !== "pending").slice(0, 8)); } catch { /* 서버 다운 시 조용히 */ }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);
  const decide = async (id: string, d: "approve" | "deny") => { await api.decide(id, d); await load(); onDecided(); };
  return (
    <div>
      {list.length === 0 ? <div className="muted small">대기 중인 승인이 없습니다. 채팅이나 잡에서 변경 도구가 호출되면 여기에 뜹니다.</div>
        : list.map((a) => <ApprovalCard key={a.id} a={{ approval_id: a.id, tool: a.tool, arguments: a.args, tier: a.tier, run_id: a.run_id }} onDecide={decide} />)}
      {history.length > 0 && (
        <>
          <h2 style={{ marginTop: 12 }}>최근 결정</h2>
          <table><tbody>
            {history.map((a) => <tr key={a.id}><td>{a.tool}</td><td className={`st-${a.status}`}>{a.status}</td><td className="muted">{a.decided_by ?? ""}</td><td className="muted">{(a.decided_at ?? a.created_at).slice(5, 16)}</td></tr>)}
          </tbody></table>
        </>
      )}
    </div>
  );
}
