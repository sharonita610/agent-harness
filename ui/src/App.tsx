import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import Chat from "./components/Chat";
import Approvals from "./components/Approvals";
import Jobs from "./components/Jobs";
import Runs from "./components/Runs";
import RunDetail from "./components/RunDetail";
import type { Health } from "./types";

type Tab = "approvals" | "jobs" | "runs";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("runs");
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const refreshHealth = useCallback(async () => {
    try { setHealth(await api.health()); setHealthErr(null); } catch (e) { setHealthErr(String(e)); }
  }, []);
  useEffect(() => { refreshHealth(); const t = setInterval(refreshHealth, 5000); return () => clearInterval(t); }, [refreshHealth]);

  const newSession = useCallback(async () => { const s = await api.createSession(); setSessionId(s.id); return s.id; }, []);

  return (
    <div className="app">
      <div className="header">
        <h1>회사 데이터 비서</h1>
        <span className="sub">
          {health ? `도구 ${health.tools}개 · 프로바이더 ${health.provider} · 대기 승인 ${health.pending_approvals}` : healthErr ? "서버 연결 안 됨" : "연결 중…"}
          {" · "}<a href="/docs" target="_blank" rel="noreferrer">API 문서</a>
        </span>
      </div>
      <div className="layout">
        <div>
          <Chat sessionId={sessionId} ensureSession={newSession} onNewSession={newSession}
                onRunFinished={(runId) => { setRefreshKey((k) => k + 1); setSelectedRun(runId); refreshHealth(); }} />
        </div>
        <div>
          <div className="card">
            <div className="tabs">
              {(["runs", "jobs", "approvals"] as Tab[]).map((t) => (
                <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
                  {t === "runs" ? "실행 기록" : t === "jobs" ? "비동기 잡" : `승인 대기${health?.pending_approvals ? ` (${health.pending_approvals})` : ""}`}
                </button>
              ))}
            </div>
            {tab === "runs" && <Runs refreshKey={refreshKey} selected={selectedRun} onSelect={setSelectedRun} />}
            {tab === "jobs" && <Jobs sessionId={sessionId} ensureSession={newSession} selected={selectedRun} onSelect={setSelectedRun} />}
            {tab === "approvals" && <Approvals onDecided={refreshHealth} />}
          </div>
        </div>
      </div>
      {selectedRun && <RunDetail runId={selectedRun} refreshKey={refreshKey} onClose={() => setSelectedRun(null)} />}
    </div>
  );
}
