import { useState } from "react";

export interface PendingApproval { approval_id: string; tool: string; arguments: Record<string, unknown>; tier: string; run_id?: string }

const TOOL_KO: Record<string, string> = { create_note: "주문에 메모 남기기", update_order_status: "주문 상태 변경", apply_discount: "할인 적용" };
const ARG_KO: Record<string, string> = { order_id: "주문 번호", note: "메모", author: "작성자", new_status: "바꿀 상태", reason: "사유" };

/** 결재 전표. 승인하면 빨간 도장, 반려하면 회색 도장이 찍힌다. */
export default function ApprovalSlip({ a, onDecide, decided, canApprove = true, decidedBy }: { a: PendingApproval; onDecide: (id: string, d: "approve" | "deny") => Promise<void>; decided?: "approve" | "deny" | null; canApprove?: boolean; decidedBy?: string | null }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<"approve" | "deny" | null>(decided ?? null);
  const [err, setErr] = useState<string | null>(null);
  const act = async (d: "approve" | "deny") => { setBusy(true); setErr(null); try { await onDecide(a.approval_id, d); setDone(d); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } };
  return (
    <div className="slip" aria-live="polite">
      {done && <div className={`stamp ${done === "deny" ? "deny" : ""}`} aria-label={done === "approve" ? "승인됨" : "반려됨"}>{done === "approve" ? "승인" : "반려"}</div>}
      <p className="title">결재 요청</p>
      <p className="sub">{TOOL_KO[a.tool] ?? a.tool}. 실행하면 데이터가 바뀝니다{a.run_id ? ` (실행 ${a.run_id.slice(4, 12)})` : ""}.</p>
      <table><tbody>
        {Object.entries(a.arguments).map(([k, v]) => <tr key={k}><td>{ARG_KO[k] ?? k}</td><td>{typeof v === "string" ? v : JSON.stringify(v)}</td></tr>)}
      </tbody></table>
      {done && decidedBy && <p className="sub" style={{ margin: 0 }}>결재자 {decidedBy}</p>}
      {!done && canApprove && (
        <div className="actions">
          <button className="primary" disabled={busy} onClick={() => act("approve")}>승인하고 실행</button>
          <button disabled={busy} onClick={() => act("deny")}>반려</button>
        </div>
      )}
      {!done && !canApprove && <p className="sub" style={{ margin: 0 }}>결재자 권한이 있는 사람이 승인해야 합니다. 결재 탭에서 결재자가 처리할 때까지 기다립니다.</p>}
      {err && <p className="error" style={{ margin: "6px 0 0" }}>{err}</p>}
    </div>
  );
}
