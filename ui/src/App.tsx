import { useCallback, useEffect, useState } from "react";
import { api, type Me } from "./api";
import Login from "./components/Login";
import Chat from "./components/Chat";
import Approvals from "./components/Approvals";
import Jobs from "./components/Jobs";
import Runs from "./components/Runs";
import RunDetail from "./components/RunDetail";
import { setToolLabels } from "./components/EventList";
import type { Health } from "./types";

type Tab = "runs" | "jobs" | "approvals";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [authErr, setAuthErr] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("runs");
  const [selectedRun, setSelectedRunState] = useState<string | null>(() => new URLSearchParams(location.search).get("run"));
  const setSelectedRun = useCallback((id: string | null) => {
    setSelectedRunState(id);
    const u = new URL(location.href); id ? u.searchParams.set("run", id) : u.searchParams.delete("run"); history.replaceState(null, "", u);
  }, []);
  const [refreshKey, setRefreshKey] = useState(0);

  const refreshHealth = useCallback(async () => {
    try { const h = await api.health(); setToolLabels(h.pack?.tool_labels); setHealth(h); setDown(false); } catch { setDown(true); }
    try { setMe(await api.me()); setAuthErr(null); } catch (e) { setMe(null); setAuthErr(String((e as Error).message)); }
  }, []);
  useEffect(() => { refreshHealth(); const t = setInterval(refreshHealth, 5000); return () => clearInterval(t); }, [refreshHealth]);
  useEffect(() => { if (health?.pack?.title) document.title = health.pack.title; }, [health?.pack?.title]);
  useEffect(() => { const th = new URLSearchParams(location.search).get("theme"); if (th === "light" || th === "dark") document.documentElement.dataset.theme = th; }, []);  // 캡처·시연용 ?theme=light
  const newSession = useCallback(async () => { const s = await api.createSession(); setSessionId(s.id); return s.id; }, []);

  return (
    <div className="app">
      <header className="masthead">
        <div>
          <h1>{health?.pack?.title ?? "비서"}</h1>
          <p>{health?.pack?.welcome ?? "도구를 호출해 답하고, 바꾸는 일은 결재를 받은 뒤에만 합니다."}</p>
        </div>
        <div className="status">
          <span><i className={`dot ${down ? "off" : ""}`} />{down ? "서버에 연결되지 않았습니다" : health ? <>도구 <b>{health.tools}개</b></> : "연결 확인 중"}</span>
          {health && <span>모델 <b>{health.provider}</b>{health.model && !health.model.hosted && (health.model.reachable ? <span className="st-ok"> 연결됨</span> : <span className="st-failed"> 꺼짐</span>)}</span>}
          {health && health.pending_approvals > 0 && <span>결재 대기 <b className="st-running">{health.pending_approvals}건</b></span>}
          <a href="/docs" target="_blank" rel="noreferrer">API 문서</a>
          <Login me={me} onChange={refreshHealth} />
        </div>
      </header>
      {authErr && health && !down && <p className="notice">이 서비스는 API 키가 필요합니다. 오른쪽 위에 운영자나 결재자 키를 넣고 로그인하세요.</p>}
      {health?.model && !health.model.hosted && !health.model.reachable && <p className="notice">모델 서버가 꺼져 있어 질문할 수 없습니다. 터미널에서 <code>scripts/serve_llm.sh</code> 를 실행하면 10초 안에 다시 연결됩니다.</p>}

      <div className="layout">
        <main>
          <Chat sessionId={sessionId} ensureSession={newSession} onNewSession={newSession} canApprove={me?.can.approve ?? false} canChat={(me?.can.chat ?? false) && (health?.model ? health.model.hosted || health.model.reachable : true)}
                examples={health?.pack?.examples ?? []} emptyHint={health?.pack?.empty_hint ?? "질문을 보내면 비서가 한 일을 순서대로 보여줍니다."}
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
