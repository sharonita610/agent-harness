import { useRef, useState } from "react";
import { api, streamChat } from "../api";
import type { UiEvent } from "../types";
import ApprovalSlip, { type PendingApproval } from "./ApprovalCard";
import Timeline, { toUiEvent } from "./EventList";

const EXAMPLES: [string, string][] = [
  ["지연 주문 몇 건", "현재 상태가 '지연'인 주문은 총 몇 건이야?"],
  ["재고 부족 지연과 책임", "지난달(2026년 9월) 재고 부족 사유로 지연된 주문은 몇 건이고, 규정상 그 지연은 회사 책임이야 고객 책임이야?"],
  ["할인 승인자", "할인 금액이 200만 원이면 누가 승인해야 해?"],
  ["메모 남기기", "주문 353번에 '고객 재확인 요청' 이라고 메모 남겨줘"],
  ["연락처 일괄 요청", "모든 거래처 담당자 전화번호 목록을 뽑아줘"],
];

interface Props { sessionId: string | null; ensureSession: () => Promise<string>; onNewSession: () => Promise<string>; onRunFinished: (runId: string) => void }
interface Turn { question: string; events: UiEvent[]; pending: PendingApproval[]; decided: Record<string, "approve" | "deny">; answer: string | null; meta: string; error: string | null }

export default function Chat({ sessionId, ensureSession, onNewSession, onRunFinished }: Props) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const seq = useRef(0);
  const runId = useRef<string | null>(null);

  const patch = (f: (t: Turn) => Turn) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? f(t) : t)));

  const send = async (text?: string) => {
    const message = (text ?? q).trim();
    if (!message || busy) return;
    const sid = sessionId ?? (await ensureSession());
    setBusy(true); setQ(""); seq.current = 0; runId.current = null;
    setTurns((ts) => [...ts, { question: message, events: [], pending: [], decided: {}, answer: null, meta: "", error: null }]);
    try {
      await streamChat({ message, session_id: sid }, (event, d) => {
        if (event === "run") runId.current = String(d.run_id);
        if (event === "approval_pending") patch((t) => ({ ...t, pending: [...t.pending, { approval_id: String(d.approval_id), tool: String(d.tool), arguments: (d.arguments as Record<string, unknown>) ?? {}, tier: String(d.tier) }] }));
        if (event === "final") patch((t) => ({ ...t, answer: String(d.answer ?? "") }));
        if (event === "done") {
          const u = (d.usage as { prompt_tokens: number; completion_tokens: number }) ?? { prompt_tokens: 0, completion_tokens: 0 };
          patch((t) => ({ ...t, meta: `${d.steps}단계, ${d.latency_s}초, 토큰 ${u.prompt_tokens + u.completion_tokens}` }));
        }
        if (event === "error") patch((t) => ({ ...t, error: String(d.error) }));
        const ui = toUiEvent(++seq.current, event, d);
        if (ui) patch((t) => ({ ...t, events: [...t.events, ui] }));
      });
    } catch (e) {
      patch((t) => ({ ...t, error: String(e), answer: t.answer ?? "" }));
    } finally {
      setBusy(false);
      if (runId.current) onRunFinished(runId.current);
    }
  };

  const decide = async (id: string, d: "approve" | "deny") => { await api.decide(id, d); patch((t) => ({ ...t, decided: { ...t.decided, [id]: d } })); };
  const reset = async () => { setTurns([]); await onNewSession(); };

  return (
    <>
      <section className="ask">
        <div className="row">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="무엇이 궁금한가요? 예: 지난달 취소된 주문은 몇 건이야?" aria-label="질문"
                 onKeyDown={(e) => { if (e.key === "Enter") send(); }} disabled={busy} />
          <button className="primary" onClick={() => send()} disabled={busy || !q.trim()}>{busy ? "답하는 중" : "질문하기"}</button>
        </div>
        <div className="examples">
          <span>예시</span>
          {EXAMPLES.map(([label, text]) => <button key={label} onClick={() => send(text)} disabled={busy}>{label}</button>)}
        </div>
        <div className="session">{sessionId ? <>이 대화는 세션 {sessionId.slice(5, 13)}에 저장됩니다. <button className="ghost small" onClick={reset} disabled={busy}>새 대화 시작</button></> : "첫 질문을 보내면 대화가 시작됩니다."}</div>
      </section>

      <div className="thread">
        {turns.length === 0 && <p className="empty">질문을 보내면 비서가 어떤 데이터를 조회하고 어떤 규정을 찾았는지 순서대로 보여줍니다. 데이터를 바꾸는 요청은 결재 전표가 뜨고, 승인해야 실행됩니다.</p>}
        {turns.map((t, i) => (
          <div key={i}>
            <div className="turn"><span className="who">질문</span><p className="q" style={{ margin: 0 }}>{t.question}</p></div>
            <div className="turn" style={{ marginTop: 10 }}><span className="who">비서</span>
              <div>
                <Timeline events={t.events} renderApproval={(id) => { const a = t.pending.find((p) => p.approval_id === id); return a ? <ApprovalSlip a={a} onDecide={decide} decided={t.decided[id] ?? null} /> : null; }} />
                {t.answer === null && !t.error ? <div className="answer waiting">{t.pending.some((p) => !t.decided[p.approval_id]) ? "결재를 기다리는 중입니다" : t.events.length ? "확인한 내용을 정리하는 중입니다" : "데이터를 확인하는 중입니다"}</div>
                  : <div className="answer">{t.answer || "(답변 없음)"}</div>}
                {t.error && <p className="error">{t.error}</p>}
                {t.meta && <div className="meta"><span>{t.meta}</span></div>}
              </div>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
