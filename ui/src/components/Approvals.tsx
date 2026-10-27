import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Approval } from "../types";
import ApprovalSlip from "./ApprovalCard";

export default function Approvals({ onDecided }: { onDecided: () => void }) {
  const [list, setList] = useState<Approval[]>([]);
  const [history, setHistory] = useState<Approval[]>([]);
  const load = useCallback(async () => {
    try { setList(await api.approvals("pending")); setHistory((await api.approvals("all")).filter((a) => a.status !== "pending").slice(0, 8)); } catch { /* 서버 다운 */ }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);
  const decide = async (id: string, d: "approve" | "deny") => { await api.decide(id, d); await load(); onDecided(); };
  const STATUS_KO: Record<string, string> = { approved: "승인", denied: "반려", expired: "기한 만료" };
  return (
    <div>
      {list.length === 0 ? <p className="empty">기다리는 결재가 없습니다. 비서가 데이터를 바꾸려 하면 여기와 대화창에 전표가 뜹니다.</p>
        : list.map((a) => <ApprovalSlip key={a.id} a={{ approval_id: a.id, tool: a.tool, arguments: a.args, tier: a.tier, run_id: a.run_id }} onDecide={decide} />)}
      {history.length > 0 && (
        <div className="history">
          <h2>최근 결재</h2>
          {history.map((a) => <div key={a.id}><span className={`st-${a.status}`}>{STATUS_KO[a.status] ?? a.status}</span><span>{a.tool}</span><span>{(a.decided_at ?? a.created_at).slice(5, 16).replace("T", " ")}</span></div>)}
        </div>
      )}
    </div>
  );
}
