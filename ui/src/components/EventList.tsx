import type { UiEvent } from "../types";
import ResultCard from "./ResultCard";

/** 도구 이름 → 사람 말. 기본값은 회사 데이터 팩이고, 서버의 /health 가 주는 팩 라벨로 덮어쓴다. */
const TOOL_KO: Record<string, string> = {
  list_tables: "테이블 목록 확인",
  describe_table: "테이블 구조 확인",
  run_sql: "데이터 조회",
  get_order: "주문 상세 조회",
  get_customer: "거래처 조회",
  search_docs: "규정 문서 검색",
  list_docs: "문서 목록 확인",
  update_order_status: "주문 상태 변경",
  create_note: "메모 남기기",
  delete_order: "주문 삭제",
};
export function setToolLabels(labels: Record<string, string> | undefined) {
  if (labels) Object.assign(TOOL_KO, labels);
}
export function toolLabel(name: string) {
  return TOOL_KO[name] ?? name;
}
const asText = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v ?? ""));
const IMAGE = /\.(svg|png|jpe?g|webp)(\?.*)?$/i;

/** 도구별로 '무엇을 보냈는지' 한 줄 요약. 넷리스트 전문 같은 긴 값은 접힌 원문에만 둔다. */
export function briefArgs(name: string, args: Record<string, unknown>): string {
  switch (name) {
    case "run_sql":
      return asText(args.sql);
    case "search_docs":
    case "search_manual":
    case "search_rules":
      return `"${asText(args.query)}"`;
    case "run_simulation": {
      const m = (args.measurements as string[]) ?? [];
      return m.length ? m.join(" · ") : "측정식 없음(동작점)";
    }
    case "run_sweep": {
      const vals = (args.values as number[]) ?? [];
      const n = vals.length || Number(args.points ?? 0);
      return `${asText(args.component)} ${n}점 · ${asText(args.measurement)}`;
    }
    case "plot_waveform":
      return (
        [args.title, args.mark_x_label, args.mark_y_label].filter(Boolean).map(asText).join(" · ") ||
        asText(args.run_id)
      );
    case "check_netlist":
    case "save_circuit":
      return asText(args.name ?? "") || `${String(args.netlist ?? "").split("\n").length}줄`;
    case "delegate":
      return `${asText(args.agent)} 비서에게: ${asText(args.question)}`;
    default:
      return Object.values(args)
        .filter((v) => v !== null && v !== undefined && v !== "")
        .map(asText)
        .join(", ");
  }
}

/** 서버 이벤트 → 한 줄. label 은 사람 말, detail 은 접혀 있는 원문, result 는 도구 결과 원본. */
export function toUiEvent(id: number, event: string, d: Record<string, unknown>): UiEvent | null {
  switch (event) {
    case "tool_call": {
      const name = String(d.name);
      const args = (d.arguments as Record<string, unknown>) ?? {};
      const full = ["run_simulation", "run_sweep", "check_netlist", "save_circuit"].includes(name)
        ? asText(args.netlist)
        : "";
      return {
        id,
        kind: event,
        label: toolLabel(name),
        detail: briefArgs(name, args) + (full ? "\n\n" + full : ""),
        tone: "tool",
        tool: name,
        args,
      };
    }
    case "tool_result": {
      const url = typeof d.url === "string" && IMAGE.test(d.url) ? d.url : undefined;
      const name = typeof d.name === "string" ? d.name : undefined;
      return {
        id,
        kind: event,
        label: url ? "그래프" : "결과",
        detail: asText(d.preview),
        tone: "",
        image: url,
        result: d.result,
        tool: name,
      };
    }
    case "tool_error":
      return {
        id,
        kind: event,
        label: "도구 오류",
        detail: asText(d.error),
        tone: "err",
        tool: typeof d.name === "string" ? d.name : undefined,
      };
    case "nudge":
      return {
        id,
        kind: event,
        label: "다시 요청",
        detail:
          d.reason === "truncated_empty"
            ? "출력이 길이 제한에 걸려 비어서 짧게 다시 요청했습니다"
            : "도구를 글로만 써서 실제 호출을 요청했습니다",
        tone: "warn",
      };
    case "approval_pending":
      return {
        id,
        kind: event,
        label: "결재 대기",
        detail: toolLabel(String(d.tool)),
        tone: "warn",
        approvalId: String(d.approval_id),
      };
    case "approval_result":
      return {
        id,
        kind: event,
        label: d.approved ? "승인됨" : "반려됨",
        detail: d.approved ? "도구를 실행합니다" : "실행하지 않습니다",
        tone: d.approved ? "ok" : "err",
      };
    case "denied":
      return {
        id,
        kind: event,
        label: "차단",
        detail: `${toolLabel(String(d.name))}은(는) 이 비서가 실행할 수 없는 작업입니다`,
        tone: "err",
      };
    case "stopped":
      return {
        id,
        kind: event,
        label: "중단",
        detail: `${asText(d.reason)} ${asText(d.error ?? "")}`.trim(),
        tone: "err",
      };
    case "error":
      return { id, kind: event, label: "오류", detail: asText(d.error), tone: "err" };
    case "run":
    case "step":
    case "llm":
    case "delta":
    case "approval_request":
    case "final":
    case "done":
    case "progress":
      return null;
    default:
      return { id, kind: event, label: event, detail: asText(d), tone: "" };
  }
}

/** 확인 과정. showResults 가 켜져 있으면 도구 결과를 카드로, 아니면 접힌 원문으로만 보여준다. */
export default function Timeline({
  events,
  renderApproval,
  showResults = true,
}: {
  events: UiEvent[];
  renderApproval?: (approvalId: string) => React.ReactNode;
  showResults?: boolean;
}) {
  if (!events.length) return null;
  return (
    <div className="timeline">
      {events.map((e) => (
        <div className={`step ${e.tone}`} key={e.id}>
          <div className="head">
            <span className="label">{e.label}</span>
            {e.kind !== "tool_error" &&
              !(showResults && e.kind === "tool_result" && e.result !== undefined) && (
                <span className="brief">{e.detail.split("\n")[0]}</span>
              )}
          </div>
          {showResults && e.kind === "tool_result" && e.result !== undefined ? (
            <ResultCard result={e.result} tool={e.tool} />
          ) : (
            <>
              {e.image && (
                <a href={e.image} target="_blank" rel="noreferrer">
                  <img className="plot" src={e.image} alt="그래프" />
                </a>
              )}
              {(e.kind === "tool_result" || e.kind === "tool_error" || e.kind === "tool_call") &&
                e.detail && (
                  <details>
                    <summary>{e.kind === "tool_call" ? "보낸 값 보기" : "원문 보기"}</summary>
                    <pre>{e.detail}</pre>
                  </details>
                )}
            </>
          )}
          {e.approvalId && renderApproval?.(e.approvalId)}
        </div>
      ))}
    </div>
  );
}
