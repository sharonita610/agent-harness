import { useCallback, useEffect, useState } from "react";
import { api, type Me } from "./api";
import Login from "./components/Login";
import Chat from "./components/Chat";
import Approvals from "./components/Approvals";
import Jobs from "./components/Jobs";
import Runs from "./components/Runs";
import RunDetail from "./components/RunDetail";
import type { Health } from "./types";

type Tab = "runs" | "jobs" | "approvals";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [authErr, setAuthErr] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("runs");
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const refreshHealth = useCallback(async () => {
    try { setHealth(await api.health()); setDown(false); } catch { setDown(true); }
    try { setMe(await api.me()); setAuthErr(null); } catch (e) { setMe(null); setAuthErr(String((e as Error).message)); }
  }, []);
  useEffect(() => { refreshHealth(); const t = setInterval(refreshHealth, 5000); return () => clearInterval(t); }, [refreshHealth]);
  const newSession = useCallback(async () => { const s = await api.createSession(); setSessionId(s.id); return s.id; }, []);

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <h1>한빛상사 비서</h1>
          <p>주문과 재고를 조회하고 규정을 찾아 답합니다. 데이터를 바꾸는 일은 결재를 받은 뒤에만 합니다.</p>
        </div>
        <div className="status">
          <span><i className={`dot ${down ? "off" : ""}`} />{down ? "서버에 연결되지 않았습니다" : health ? <>도구 <b>{health.tools}개</b></> : "연결 확인 중"}</span>
          {health && <span>모델 <b>{health.provider}</b></span>}
          {health && health.pending_approvals > 0 && <span>결재 대기 <b className="st-running">{health.pending_approvals}건</b></span>}
          <a href="/docs" target="_blank" rel="noreferrer">API 문서</a>
          <Login me={me} onChange={refreshHealth} />
        </div>
      </header>
      {authErr && health && !down && <p className="notice">이 서비스는 API 키가 필요합니다. 운영자나 결재자 키로 로그인하세요. ({authErr})</p>}

      <div className="layout">
        <main>
          <Chat sessionId={sessionId} ensureSession={newSession} onNewSession={newSession} canApprove={me?.can.approve ?? false} canChat={me?.can.chat ?? false}
                onRunFinished={(runId) => { setRefreshKey((k) => k + 1); setSelectedRun(runId); refreshHealth(); }} />
        </main>
        <aside className="ledger">
          <div className="tabs" role="tablist">
            {(["runs", "jobs", "approvals"] as Tab[]).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
                {t === "runs" ? "실행 기록" : t === "jobs" ? "예약 작업" : health?.pending_approvals ? `결재 ${health.pending_approvals}` : "결재"}
              </button>
            ))}
          </div>
          {tab === "runs" && <Runs refreshKey={refreshKey} selected={selectedRun} onSelect={setSelectedRun} />}
          {tab === "jobs" && <Jobs sessionId={sessionId} ensureSession={newSession} selected={selectedRun} onSelect={setSelectedRun} />}
          {tab === "approvals" && <Approvals onDecided={refreshHealth} canApprove={me?.can.approve ?? false} />}
        </aside>
      </div>

      {selectedRun && <RunDetail runId={selectedRun} refreshKey={refreshKey} onClose={() => setSelectedRun(null)} />}
    </div>
  );
}
