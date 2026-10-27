import { useState } from "react";

export interface PendingApproval { approval_id: string; tool: string; arguments: Record<string, unknown>; tier: string; run_id?: string }

export default function ApprovalCard({ a, onDecide }: { a: PendingApproval; onDecide: (id: string, d: "approve" | "deny") => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<"approve" | "deny" | null>(null);
  const act = async (d: "approve" | "deny") => { setBusy(true); try { await onDecide(a.approval_id, d); setDone(d); } finally { setBusy(false); } };
  return (
    <div className="approval">
      <b>승인 요청 · {a.tool} <span className="pill">{a.tier}</span>{a.run_id && <span className="pill">{a.run_id.slice(4, 12)}</span>}</b>
      <pre>{JSON.stringify(a.arguments, null, 1)}</pre>
      {done ? <span className={done === "approve" ? "st-ok" : "st-denied"}>{done === "approve" ? "승인함" : "거부함"}</span> : (
        <div className="row">
          <button className="primary" disabled={busy} onClick={() => act("approve")}>승인</button>
          <button disabled={busy} onClick={() => act("deny")}>거부</button>
        </div>
      )}
    </div>
  );
}
