import { useEffect, useState } from "react";
import { apiAt } from "../api";
import type { Approval, Run, RunEvent, Trace } from "../types";
import TraceView from "./TraceView";
import Rich from "./Rich";
import Timeline, { toUiEvent } from "./EventList";

const STATUS_KO: Record<string, string> = {
  done: "완료",
  failed: "실패",
  cancelled: "취소됨",
  running: "실행 중",
  queued: "대기",
};

/** 실행 상세 드로어: 답, 비서가 한 일(결과 카드 포함), 시간 분포, 이벤트 원문. Esc 나 바깥 클릭으로 닫는다. */
export default function Drawer({
  base,
  runId,
  refreshKey,
  onClose,
}: {
  base: string;
  runId: string;
  refreshKey: number;
  onClose: () => void;
}) {
  const [run, setRun] = useState<(Run & { events: RunEvent[]; approvals: Approval[] }) | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [showEvents, setShowEvents] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const api = apiAt(base);
    (async () => {
      try {
        const [r, t] = await Promise.all([api.run(runId), api.trace(runId)]);
        if (alive) {
          setRun(r);
          setTrace(t);
          setErr(null);
        }
      } catch (e) {
        if (alive) setErr((e as Error).message);
      }
    })();
    return () => {
      alive = false;
    };
  }, [base, runId, refreshKey]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <section className="drawer" role="dialog" aria-label="실행 상세">
        <header>
          <h2>
            실행 상세{" "}
            <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>
              {runId}
            </span>
          </h2>
          <button className="ghost small" onClick={onClose}>
            닫기
          </button>
        </header>
        {err && <p className="error">{err}</p>}
        {!err && (!run || !trace) && <p className="muted">불러오는 중</p>}
        {run && trace && (
          <>
            <p className="q">
              <span className="muted">질문 </span>
              {run.question}
            </p>
            <div className="answer">
              <Rich text={run.answer ?? run.error ?? "(아직 답이 없습니다)"} />
            </div>
            <div className="meta">
              <span className={`st-${run.status}`}>{STATUS_KO[run.status] ?? run.status}</span>
              <span>{run.steps ?? "-"}단계</span>
              <span>{run.latency_s ?? "-"}초</span>
              <span>
                토큰 {((run.prompt_tokens ?? 0) + (run.completion_tokens ?? 0)).toLocaleString("ko-KR")}
              </span>
              {run.approvals.length > 0 && (
                <span>
                  결재{" "}
                  {run.approvals
                    .map(
                      (a) =>
                        `${a.tool} ${a.status === "approved" ? "승인" : a.status === "denied" ? "반려" : a.status}`,
                    )
                    .join(", ")}
                </span>
              )}
            </div>
            <h3>비서가 한 일</h3>
            <Timeline
              events={run.events
                .map((e) => toUiEvent(e.seq, e.kind, e.data))
                .filter((e): e is NonNullable<typeof e> => e !== null)}
            />
            <h3>시간이 어디에 쓰였나</h3>
            <TraceView trace={trace} />
            <button
              className="ghost small"
              style={{ marginTop: 10 }}
              onClick={() => setShowEvents((v) => !v)}
            >
              {showEvents ? "이벤트 원문 숨기기" : `이벤트 원문 ${run.events.length}개 보기`}
            </button>
            {showEvents && (
              <table className="events">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>시각</th>
                    <th>종류</th>
                    <th>내용</th>
                  </tr>
                </thead>
                <tbody>
                  {run.events.map((e) => (
                    <tr key={e.seq}>
                      <td className="muted">{e.seq}</td>
                      <td className="muted">{e.ts.slice(11, 23)}</td>
                      <td>{e.kind}</td>
                      <td>
                        <pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                          {JSON.stringify(e.data).slice(0, 300)}
                        </pre>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </section>
    </>
  );
}
