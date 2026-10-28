import { useCallback, useEffect, useMemo, useState } from "react";
import { apiAt } from "../api";
import type { Run } from "../types";

const STATUS_KO: Record<string, string> = { queued: "대기", running: "실행 중", done: "완료", failed: "실패", cancelled: "취소됨" };
const KIND_KO: Record<string, string> = { chat: "질문", job: "맡긴 작업", a2a: "위임받음" };

/** "기록" 탭: 이 비서의 실행 전부. 상태·종류·검색으로 거르고, 누르면 작업지로 다시 연다. */
export default function History({ base, agentName, onOpen, onDetail }: { base: string; agentName: string; onOpen: (runId: string) => void; onDetail: (runId: string) => void }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [status, setStatus] = useState(""); const [kind, setKind] = useState(""); const [text, setText] = useState("");
  const load = useCallback(async () => { try { setRuns(await apiAt(base).runs(100)); } catch { /* 서버 다운 */ } }, [base]);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);
  const shown = useMemo(() => runs.filter((r) => (!status || r.status === status) && (!kind || r.kind === kind) && (!text || r.question.includes(text) || (r.answer ?? "").includes(text))), [runs, status, kind, text]);
  return (
    <main className="desk page">
      <div className="deskhead"><h1>{agentName} · 기록</h1><p>모든 실행이 저장됩니다. 누르면 그때의 답과 근거를 작업지로 다시 엽니다.</p></div>
      <div className="filters">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="질문이나 답에서 찾기" aria-label="검색" />
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="종류"><option value="">모든 종류</option>{Object.entries(KIND_KO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="상태"><option value="">모든 상태</option>{Object.entries(STATUS_KO).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <span className="muted small">{shown.length}건</span>
      </div>
      {shown.length === 0 ? <p className="empty">기록이 없습니다.</p> : (
        <div className="tablewrap"><table className="data wide">
          <thead><tr><th>시각</th><th>질문</th><th>종류</th><th>상태</th><th>단계</th><th>소요</th><th></th></tr></thead>
          <tbody>{shown.map((r) => <tr key={r.id} className="rowbtn" onClick={() => onOpen(r.id)} title={r.id}>
            <td>{r.created_at.slice(5, 16).replace("T", " ")}</td><td className="wrap">{r.question}</td><td>{KIND_KO[r.kind] ?? r.kind}</td>
            <td><span className={`st-${r.status}`}>{STATUS_KO[r.status] ?? r.status}</span></td><td className="num">{r.steps ?? ""}</td><td className="num">{r.latency_s != null ? `${r.latency_s}초` : ""}</td>
            <td><button className="ghost small" onClick={(e) => { e.stopPropagation(); onDetail(r.id); }}>실행 상세</button></td></tr>)}</tbody>
        </table></div>
      )}
    </main>
  );
}
