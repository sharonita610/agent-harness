import { useEffect, useRef, useState } from "react";
import { apiAt, streamChat } from "../api";
import type { UiEvent } from "../types";
import ApprovalSlip, { type PendingApproval } from "./ApprovalCard";
import Timeline, { briefArgs, toolLabel, toUiEvent } from "./EventList";
import ResultCard from "./ResultCard";
import Rich from "./Rich";

export interface Sheet {
  id: number;
  base: string;
  agent: string;
  question: string;
  events: UiEvent[];
  pending: PendingApproval[];
  decided: Record<string, "approve" | "deny">;
  answer: string | null;
  draft: string;
  meta: string;
  error: string | null;
  runId: string | null;
  at: string;
  progress?: { name: string; done: number; total: number; label: string } | null;
}

interface Props {
  base: string;
  agentName: string;
  welcome: string;
  examples: string[][];
  emptyHint: string;
  sheets: Sheet[];
  setSheets: (f: (s: Sheet[]) => Sheet[]) => void;
  ensureSession: (base: string) => Promise<string>;
  onRunFinished: (base: string, runId: string) => void;
  onOpenRun: (runId: string) => void;
  canApprove: boolean;
  canChat: boolean;
  blockedWhy: string | null;
  onData?: () => void;
}

/** 근거로 보여줄 가치가 없는 '둘러보기' 도구. 확인 과정에는 남는다. */
const QUIET_TOOLS = /^(list_|describe_table$|check_netlist$|check_task$|get_waveform$)/;

export default function Desk({
  base,
  agentName,
  welcome,
  examples,
  emptyHint,
  sheets,
  setSheets,
  ensureSession,
  onRunFinished,
  onOpenRun,
  canApprove,
  canChat,
  blockedWhy,
  onData,
}: Props) {
  const [q, setQ] = useState("");
  const [busyBases, setBusyBases] = useState<Set<string>>(new Set()); // 비서별로 따로 바쁘다. 한 비서가 결재를 기다려도 다른 비서에게 물을 수 있다
  const busy = busyBases.has(base);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const nextId = useRef(1);
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    if (!q) {
      el.style.height = "";
      return;
    }
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  }, [q]); // 비면 기본 높이로(숨은 탭에서 잰 높이가 남지 않게)

  const patch = (id: number, f: (t: Sheet) => Sheet) =>
    setSheets((ss) => ss.map((t) => (t.id === id ? f(t) : t)));

  const send = async (text?: string) => {
    const message = (text ?? q).trim();
    if (!message || busy || !canChat) return;
    const myBase = base;
    const sid = await ensureSession(myBase);
    const id = nextId.current++;
    let seq = 0;
    let runId: string | null = null;
    setBusyBases((b) => new Set(b).add(myBase));
    setQ("");
    setSheets((ss) => [
      {
        id,
        base: myBase,
        agent: agentName,
        question: message,
        events: [],
        pending: [],
        decided: {},
        answer: null,
        draft: "",
        meta: "",
        error: null,
        runId: null,
        at: new Date().toISOString(),
      },
      ...ss,
    ]);
    try {
      await streamChat({ message, session_id: sid }, (event, d) => {
        if (event === "run") {
          runId = String(d.run_id);
          patch(id, (t) => ({ ...t, runId }));
        }
        if (event === "delta") patch(id, (t) => ({ ...t, draft: t.draft + String(d.text ?? "") }));
        if (event === "tool_call") patch(id, (t) => ({ ...t, draft: "", progress: null }));
        if (event === "progress")
          patch(id, (t) => ({
            ...t,
            progress: {
              name: String(d.name),
              done: Number(d.done),
              total: Number(d.total),
              label: String(d.label ?? ""),
            },
          }));
        if (event === "tool_result" || event === "tool_error") patch(id, (t) => ({ ...t, progress: null }));
        if (event === "approval_pending")
          patch(id, (t) => ({
            ...t,
            pending: [
              ...t.pending,
              {
                approval_id: String(d.approval_id),
                tool: String(d.tool),
                arguments: (d.arguments as Record<string, unknown>) ?? {},
                tier: String(d.tier),
              },
            ],
          }));
        if (event === "approval_result")
          patch(id, (t) => ({
            ...t,
            decided: {
              ...t.decided,
              ...Object.fromEntries(
                t.pending
                  .filter((p) => !t.decided[p.approval_id])
                  .map((p) => [p.approval_id, d.approved ? "approve" : "deny"]),
              ),
            },
          })); // 결재함에서 처리돼도 작업지 전표에 도장이 찍힌다
        if (event === "final") patch(id, (t) => ({ ...t, answer: String(d.answer ?? "") }));
        if (event === "done") {
          const u = (d.usage as { prompt_tokens: number; completion_tokens: number }) ?? {
            prompt_tokens: 0,
            completion_tokens: 0,
          };
          patch(id, (t) => ({
            ...t,
            meta: `${d.latency_s}초 · 토큰 ${(u.prompt_tokens + u.completion_tokens).toLocaleString("ko-KR")}`,
          }));
        }
        if (event === "error") patch(id, (t) => ({ ...t, error: String(d.error) }));
        const ui = toUiEvent(++seq, event, d);
        if (ui) patch(id, (t) => ({ ...t, events: [...t.events, ui] }));
      });
    } catch (e) {
      patch(id, (t) => ({ ...t, error: (e as Error).message, answer: t.answer ?? "" }));
    } finally {
      setBusyBases((b) => {
        const n = new Set(b);
        n.delete(myBase);
        return n;
      });
      if (runId) onRunFinished(myBase, runId);
    }
  };

  const decide = async (sheet: Sheet, aid: string, d: "approve" | "deny") => {
    await apiAt(sheet.base).decide(aid, d);
    patch(sheet.id, (t) => ({ ...t, decided: { ...t.decided, [aid]: d } }));
  };
  const mine = sheets.filter((s) => s.base === base);

  return (
    <main className="desk">
      <div className="deskhead">
        <h1>{agentName}</h1>
        <p>{welcome}</p>
      </div>
      {blockedWhy && <p className="notice">{blockedWhy}</p>}
      <div className="compose">
        <textarea
          ref={textareaRef}
          rows={1}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={examples[0] ? `예: ${examples[0][1]}` : "무엇을 확인할까요?"}
          aria-label="질문"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          disabled={!canChat}
        />
        <button className="primary" onClick={() => send()} disabled={busy || !q.trim() || !canChat}>
          {busy ? "답하는 중" : "질문하기"}
        </button>
      </div>
      {examples.length > 0 && (
        <div className="examples">
          <span>예시</span>
          {examples.map(([label, text]) => (
            <button key={label} onClick={() => send(text)} disabled={busy || !canChat}>
              {label}
            </button>
          ))}
        </div>
      )}

      <div className="sheets">
        {mine.length === 0 && <p className="empty">{emptyHint}</p>}
        {mine.map((t) => (
          <SheetView
            key={t.id}
            t={t}
            canApprove={canApprove}
            onDecide={(aid, d) => decide(t, aid, d)}
            onOpenRun={onOpenRun}
            onData={onData}
          />
        ))}
      </div>
    </main>
  );
}

function SheetView({
  t,
  canApprove,
  onDecide,
  onOpenRun,
  onData,
}: {
  t: Sheet;
  canApprove: boolean;
  onDecide: (aid: string, d: "approve" | "deny") => Promise<void>;
  onOpenRun: (runId: string) => void;
  onData?: () => void;
}) {
  // 근거 카드: 도구 결과 중 결과 원본이 있고 둘러보기가 아닌 것. 바로 앞의 호출에서 '무엇을 보냈는지'를 제목에 붙인다.
  const cards: { ev: UiEvent; title: string; sub: string }[] = [];
  let lastCall: UiEvent | null = null;
  for (const ev of t.events) {
    if (ev.kind === "tool_call") lastCall = ev;
    if (ev.kind === "tool_result" && ev.result !== undefined && ev.tool && !QUIET_TOOLS.test(ev.tool)) {
      cards.push({
        ev,
        title: toolLabel(ev.tool),
        sub: lastCall?.tool === ev.tool ? briefArgs(ev.tool, lastCall.args ?? {}) : "",
      });
    }
  }
  const working = t.answer === null && !t.error;
  const waitingApproval = t.pending.some((p) => !t.decided[p.approval_id]);
  const toolSteps = t.events.filter((e) => e.kind === "tool_call").length;
  return (
    <article className="sheet">
      <div className="top">
        <span>
          {t.agent} · {new Date(t.at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}
        </span>
        {t.runId && (
          <button className="ghost small" onClick={() => onOpenRun(t.runId!)}>
            실행 상세
          </button>
        )}
      </div>
      <p className="q">{t.question}</p>
      {working ? (
        t.draft ? (
          <div className="answer">
            <Rich text={t.draft} />
            <span className="caret" aria-hidden="true" />
          </div>
        ) : t.progress ? (
          <div className="answer waiting">
            <span>
              {toolLabel(t.progress.name)} {t.progress.done}/{t.progress.total}
              {t.progress.label ? ` · ${t.progress.label}` : ""}
            </span>
            <span className="pbar">
              <span
                style={{
                  width: `${t.progress.total ? Math.min(100, (100 * t.progress.done) / t.progress.total) : 0}%`,
                }}
              />
            </span>
          </div>
        ) : (
          <div className="answer waiting">
            {waitingApproval
              ? "결재를 기다리고 있습니다. 아래 전표에서 승인하거나 반려하세요."
              : toolSteps
                ? "확인한 내용을 정리하는 중입니다"
                : "데이터를 확인하는 중입니다"}
          </div>
        )
      ) : (
        <div className="answer">
          <Rich text={t.answer || t.error || "(답변 없음)"} />
        </div>
      )}
      {t.error && t.answer !== null && t.answer !== "" && <p className="error">{t.error}</p>}
      {t.pending.map((p) => (
        <ApprovalSlip
          key={p.approval_id}
          a={p}
          onDecide={onDecide}
          decided={t.decided[p.approval_id] ?? null}
          canApprove={canApprove}
        />
      ))}
      {cards.length > 0 && (
        <div className="evidence">
          {cards.slice(-6).map(({ ev, title, sub }) => (
            <div className="card" key={ev.id}>
              <div className="ct">
                <b>{title}</b>
                {sub && <span title={sub}>{sub}</span>}
                {onData && (
                  <button className="ghost small" onClick={onData} title="비서 없이 원 데이터를 본다">
                    데이터 보기
                  </button>
                )}
              </div>
              <ResultCard result={ev.result} tool={ev.tool} />
            </div>
          ))}
        </div>
      )}
      {t.events.length > 0 && (
        <details className="process">
          <summary>
            확인 과정 · 도구 {toolSteps}회{t.meta ? ` · ${t.meta}` : ""}
          </summary>
          <Timeline events={t.events} showResults={false} />
        </details>
      )}
    </article>
  );
}
