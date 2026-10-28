import { useEffect, useState } from "react";
import { toolLabel } from "./EventList";

export interface PendingApproval {
  approval_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  tier: string;
  run_id?: string;
}

const ARG_KO: Record<string, string> = {
  order_id: "주문 번호",
  note: "메모",
  author: "작성자",
  new_status: "바꿀 상태",
  reason: "사유",
  name: "이름",
  netlist: "넷리스트",
  event_id: "이벤트",
  label: "라벨",
  station: "스테이션",
  title: "제목",
  description: "내용",
  owner: "담당",
  threshold_multiplier: "기준 배수",
  window_hours: "구간(시간)",
  enabled: "켬",
};
const formatValue = (v: unknown) =>
  typeof v === "string" ? v : typeof v === "boolean" ? (v ? "예" : "아니요") : JSON.stringify(v);

/** 결재 전표. 승인하면 빨간 도장, 반려하면 회색 도장이 찍힌다. from 은 어느 비서의 요청인지. */
export default function ApprovalSlip({
  a,
  onDecide,
  decided,
  canApprove = true,
  decidedBy,
  from,
}: {
  a: PendingApproval;
  onDecide: (id: string, d: "approve" | "deny") => Promise<void>;
  decided?: "approve" | "deny" | null;
  canApprove?: boolean;
  decidedBy?: string | null;
  from?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<"approve" | "deny" | null>(decided ?? null);
  useEffect(() => setDone(decided ?? null), [decided]); // 결재함에서 처리돼도 작업지 전표에 도장이 찍히고 버튼이 사라진다
  const [err, setErr] = useState<string | null>(null);
  const decide = async (d: "approve" | "deny") => {
    setBusy(true);
    setErr(null);
    try {
      await onDecide(a.approval_id, d);
      setDone(d);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="slip" aria-live="polite">
      {done && (
        <div
          className={`stamp ${done === "deny" ? "deny" : ""}`}
          aria-label={done === "approve" ? "승인됨" : "반려됨"}
        >
          {done === "approve" ? "승인" : "반려"}
        </div>
      )}
      {from && <span className="from">{from}</span>}
      <p className="title">{toolLabel(a.tool)}</p>
      <p className="sub">
        {a.tier === "destructive"
          ? "되돌릴 수 없는 작업입니다. 승인하면 바로 실행합니다."
          : "실행하면 데이터가 바뀝니다. 승인하면 바로 실행합니다."}
      </p>
      <table>
        <tbody>
          {Object.entries(a.arguments)
            .filter(([, v]) => v !== null && v !== "")
            .map(([k, v]) => (
              <tr key={k}>
                <td>{ARG_KO[k] ?? k}</td>
                <td style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                  {formatValue(v).slice(0, 400)}
                </td>
              </tr>
            ))}
        </tbody>
      </table>
      {done && decidedBy && (
        <p className="sub" style={{ margin: 0 }}>
          결재자 {decidedBy}
        </p>
      )}
      {!done && canApprove && (
        <div className="actions">
          <button className="primary" disabled={busy} onClick={() => decide("approve")}>
            승인하고 실행
          </button>
          <button disabled={busy} onClick={() => decide("deny")}>
            반려
          </button>
        </div>
      )}
      {!done && !canApprove && (
        <p className="sub" style={{ margin: 0 }}>
          결재자 권한이 있는 사람이 승인해야 합니다. 결재함에서 처리될 때까지 기다립니다.
        </p>
      )}
      {err && (
        <p className="error" style={{ margin: "6px 0 0" }}>
          {err}
        </p>
      )}
    </div>
  );
}
