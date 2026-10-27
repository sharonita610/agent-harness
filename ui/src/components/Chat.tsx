import { useRef, useState } from "react";
import { api, streamChat } from "../api";
import type { UiEvent } from "../types";
import ApprovalCard, { type PendingApproval } from "./ApprovalCard";
import EventList, { toUiEvent } from "./EventList";

const EXAMPLES = [
  ["지연 주문 몇 건", "현재 상태가 '지연'인 주문은 총 몇 건이야?"],
  ["재고 부족 지연 + 책임", "지난달(2026년 9월) 재고 부족 사유로 지연된 주문은 몇 건이고, 규정상 그 지연은 회사 책임이야 고객 책임이야?"],
  ["할인 승인자", "할인 금액이 200만 원이면 누가 승인해야 해?"],
  ["메모 남기기 (승인 필요)", "주문 353번에 '고객 재확인 요청' 이라고 메모 남겨줘"],
  ["연락처 일괄 (거절)", "모든 거래처 담당자 전화번호 목록을 뽑아줘"],
];

interface Props { sessionId: string | null; ensureSession: () => Promise<string>; onNewSession: () => Promise<string>; onRunFinished: (runId: string) => void }

export default function Chat({ sessionId, ensureSession, onNewSession, onRunFinished }: Props) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [events, setEvents] = useState<UiEvent[]>([]);
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const [answer, setAnswer] = useState("");
  const [meta, setMeta] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const seq = useRef(0);
  const runId = useRef<string | null>(null);

  const send = async (text?: string) => {
    const message = (text ?? q).trim();
    if (!message || busy) return;
    const sid = sessionId ?? (await ensureSession());
    setBusy(true); setEvents([]); setPending([]); setAnswer("…"); setMeta(""); setErr(null); seq.current = 0; runId.current = null;
    try {
      await streamChat({ message, session_id: sid }, (event, d) => {
        if (event === "run") runId.current = String(d.run_id);
        if (event === "approval_pending") setPending((p) => [...p, { approval_id: String(d.approval_id), tool: String(d.tool), arguments: (d.arguments as Record<string, unknown>) ?? {}, tier: String(d.tier) }]);
        if (event === "final") setAnswer(String(d.answer ?? ""));
        if (event === "done") {
          const u = (d.usage as { prompt_tokens: number; completion_tokens: number }) ?? { prompt_tokens: 0, completion_tokens: 0 };
          setMeta(`${d.steps}스텝 · ${d.latency_s}s · 토큰 ${u.prompt_tokens}+${u.completion_tokens} · ${d.stopped_reason} · run ${String(d.run_id).slice(4, 12)}`);
        }
        if (event === "error") setErr(String(d.error));
        const ui = toUiEvent(++seq.current, event, d);
        if (ui) setEvents((es) => [...es, ui]);
      });
    } catch (e) {
      setErr(String(e)); setAnswer("(오류)");
    } finally {
      setBusy(false); setQ("");
      if (runId.current) onRunFinished(runId.current);
    }
  };

  const decide = async (id: string, d: "approve" | "deny") => { await api.decide(id, d); };

  return (
    <>
      <div className="card">
        <h2>질문</h2>
        <div className="chips">{EXAMPLES.map(([label, text]) => <button key={label} className="chip" onClick={() => send(text)} disabled={busy}>{label}</button>)}</div>
        <div className="row">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="질문을 입력하고 Enter" onKeyDown={(e) => { if (e.key === "Enter") send(); }} disabled={busy} />
          <button className="primary" onClick={() => send()} disabled={busy}>{busy ? "실행 중…" : "보내기"}</button>
          <button onClick={() => onNewSession()} disabled={busy}>새 세션</button>
        </div>
        <div className="meta">세션: {sessionId ?? "(없음, 첫 질문 때 생성)"}</div>
      </div>
      <div className="card">
        <h2>답변</h2>
        <div className="answer">{answer || "—"}</div>
        {err && <div className="err small">{err}</div>}
        <div className="meta">{meta}</div>
      </div>
      <div className="card">
        <h2>실행 이벤트</h2>
        {pending.map((a) => <ApprovalCard key={a.approval_id} a={a} onDecide={decide} />)}
        <EventList events={events} />
      </div>
    </>
  );
}
