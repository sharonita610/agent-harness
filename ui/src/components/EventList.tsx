import type { UiEvent } from "../types";

export function toUiEvent(id: number, event: string, d: Record<string, unknown>): UiEvent | null {
  const s = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v ?? ""));
  switch (event) {
    case "run": return { id, kind: event, label: "run", detail: s(d.run_id), tone: "" };
    case "step": return { id, kind: event, label: `step ${s(d.step)}`, detail: "", tone: "" };
    case "tool_call": return { id, kind: event, label: `→ ${s(d.name)}`, detail: s(d.arguments), tone: "tool" };
    case "tool_result": return { id, kind: event, label: `← ${s(d.name)}`, detail: s(d.preview), tone: "" };
    case "tool_error": return { id, kind: event, label: `✗ ${s(d.name)}`, detail: s(d.error), tone: "err" };
    case "nudge": return { id, kind: event, label: "nudge", detail: s(d.text_head), tone: "warn" };
    case "approval_pending": return { id, kind: event, label: "승인 대기", detail: `${s(d.tool)} ${s(d.arguments)}`, tone: "warn" };
    case "approval_result": return { id, kind: event, label: "승인 결과", detail: d.approved ? "승인됨" : "거부됨", tone: d.approved ? "ok" : "err" };
    case "denied": return { id, kind: event, label: "차단", detail: `${s(d.name)} (${s(d.tier)})`, tone: "err" };
    case "stopped": return { id, kind: event, label: "중단", detail: `${s(d.reason)} ${s(d.error ?? "")}`, tone: "err" };
    case "error": return { id, kind: event, label: "오류", detail: s(d.error), tone: "err" };
    case "llm": case "approval_request": case "final": case "done": return null;
    default: return { id, kind: event, label: event, detail: s(d), tone: "" };
  }
}

export default function EventList({ events }: { events: UiEvent[] }) {
  if (!events.length) return <div className="muted small">아직 이벤트가 없습니다. 질문을 보내면 도구 호출 과정이 여기 실시간으로 쌓입니다.</div>;
  return (
    <div className="events">
      {events.map((e) => (
        <div className="ev" key={e.id}><span className={`k ${e.tone}`}>{e.label}</span><pre>{e.detail}</pre></div>
      ))}
    </div>
  );
}
