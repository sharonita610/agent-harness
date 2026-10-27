import type { UiEvent } from "../types";

const TOOL_KO: Record<string, string> = {
  list_tables: "테이블 목록 확인", describe_table: "테이블 구조 확인", run_sql: "데이터 조회", get_order: "주문 상세 조회", get_customer: "거래처 조회",
  search_docs: "규정 문서 검색", list_docs: "문서 목록 확인", update_order_status: "주문 상태 변경", create_note: "메모 남기기", delete_order: "주문 삭제",
};
const s = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v ?? ""));

/** 서버 이벤트 → 타임라인 한 줄. label 은 사람 말, detail 은 접혀 있는 원문. */
export function toUiEvent(id: number, event: string, d: Record<string, unknown>): UiEvent | null {
  switch (event) {
    case "tool_call": {
      const name = String(d.name); const args = (d.arguments as Record<string, unknown>) ?? {};
      const brief = name === "run_sql" ? s(args.sql) : name === "search_docs" ? `"${s(args.query)}"` : Object.values(args).map(s).join(", ");
      return { id, kind: event, label: TOOL_KO[name] ?? name, detail: brief, tone: "tool" };
    }
    case "tool_result": return { id, kind: event, label: "결과", detail: s(d.preview), tone: "" };
    case "tool_error": return { id, kind: event, label: "도구 오류", detail: s(d.error), tone: "err" };
    case "nudge": return { id, kind: event, label: "다시 요청", detail: "도구를 글로만 써서 실제 호출을 요청했습니다", tone: "warn" };
    case "approval_pending": return { id, kind: event, label: "결재 대기", detail: `${TOOL_KO[String(d.tool)] ?? s(d.tool)}`, tone: "warn", approvalId: String(d.approval_id) };
    case "approval_result": return { id, kind: event, label: d.approved ? "승인됨" : "반려됨", detail: d.approved ? "도구를 실행합니다" : "실행하지 않습니다", tone: d.approved ? "ok" : "err" };
    case "denied": return { id, kind: event, label: "차단", detail: `${TOOL_KO[String(d.name)] ?? s(d.name)}은(는) 이 비서가 실행할 수 없는 작업입니다`, tone: "err" };
    case "stopped": return { id, kind: event, label: "중단", detail: `${s(d.reason)} ${s(d.error ?? "")}`.trim(), tone: "err" };
    case "error": return { id, kind: event, label: "오류", detail: s(d.error), tone: "err" };
    case "run": case "step": case "llm": case "delta": case "approval_request": case "final": case "done": return null;
    default: return { id, kind: event, label: event, detail: s(d), tone: "" };
  }
}

export default function Timeline({ events, renderApproval }: { events: UiEvent[]; renderApproval?: (approvalId: string) => React.ReactNode }) {
  if (!events.length) return null;
  return (
    <div className="timeline">
      {events.map((e) => (
        <div className={`step ${e.tone}`} key={e.id}>
          <div className="head"><span className="label">{e.label}</span>{e.kind !== "tool_error" && <span className="brief">{e.detail}</span>}</div>
          {(e.kind === "tool_result" || e.kind === "tool_error" || e.kind === "tool_call") && e.detail && (
            <details><summary>{e.kind === "tool_call" ? "보낸 값 보기" : "원문 보기"}</summary><pre>{e.detail}</pre></details>
          )}
          {e.approvalId && renderApproval?.(e.approvalId)}
        </div>
      ))}
    </div>
  );
}
