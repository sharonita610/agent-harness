import { useRef, useState } from "react";
import { api, streamChat } from "../api";
import type { UiEvent } from "../types";
import ApprovalSlip, { type PendingApproval } from "./ApprovalCard";
import Timeline, { toUiEvent } from "./EventList";
import Rich from "./Rich";

interface Props { sessionId: string | null; ensureSession: () => Promise<string>; onNewSession: () => Promise<string>; onRunFinished: (runId: string) => void; canApprove: boolean; canChat: boolean; examples: string[][]; emptyHint: string }
interface Turn { question: string; events: UiEvent[]; pending: PendingApproval[]; decided: Record<string, "approve" | "deny">; answer: string | null; draft: string; meta: string; error: string | null }

export default function Chat({ sessionId, ensureSession, onNewSession, onRunFinished, canApprove, canChat, examples, emptyHint }: Props) {
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
    setTurns((ts) => [...ts, { question: message, events: [], pending: [], decided: {}, answer: null, draft: "", meta: "", error: null }]);
    try {
      await streamChat({ message, session_id: sid }, (event, d) => {
        if (event === "run") runId.current = String(d.run_id);
        if (event === "delta") patch((t) => ({ ...t, draft: t.draft + String(d.text ?? "") }));
        if (event === "tool_call") patch((t) => ({ ...t, draft: "" }));  // 도구 호출로 이어진 스텝의 중간 텍스트는 버린다
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
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={examples[0] ? `무엇이 궁금한가요? 예: ${examples[0][1]}` : "무엇이 궁금한가요?"} aria-label="질문"
                 onKeyDown={(e) => { if (e.key === "Enter") send(); }} disabled={busy || !canChat} />
          <button className="primary" onClick={() => send()} disabled={busy || !q.trim() || !canChat}>{busy ? "답하는 중" : "질문하기"}</button>
        </div>
        <div className="examples">
          <span>예시</span>
          {examples.map(([label, text]) => <button key={label} onClick={() => send(text)} disabled={busy || !canChat}>{label}</button>)}
        </div>
        <div className="session">{sessionId ? <>이 대화는 세션 {sessionId.slice(5, 13)}에 저장됩니다. <button className="ghost small" onClick={reset} disabled={busy}>새 대화 시작</button></> : "첫 질문을 보내면 대화가 시작됩니다."}</div>
      </section>

      <div className="thread">
        {turns.length === 0 && <p className="empty">{emptyHint}</p>}
        {turns.map((t, i) => (
          <div key={i}>
            <div className="turn"><span className="who">질문</span><p className="q" style={{ margin: 0 }}>{t.question}</p></div>
            <div className="turn" style={{ marginTop: 10 }}><span className="who">비서</span>
              <div>
                <Timeline events={t.events} renderApproval={(id) => { const a = t.pending.find((p) => p.approval_id === id); return a ? <ApprovalSlip a={a} onDecide={decide} decided={t.decided[id] ?? null} canApprove={canApprove} /> : null; }} />
                {t.answer === null && !t.error ? (
                  t.draft ? <div className="answer streaming"><Rich text={t.draft} /><span className="caret" aria-hidden="true" /></div>
                    : <div className="answer waiting">{t.pending.some((p) => !t.decided[p.approval_id]) ? "결재를 기다리는 중입니다" : t.events.length ? "확인한 내용을 정리하는 중입니다" : "데이터를 확인하는 중입니다"}</div>
                ) : <div className="answer"><Rich text={t.answer || "(답변 없음)"} /></div>}
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
