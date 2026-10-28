import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiAt, getBase, setBase, type Me } from "./api";
import Sidebar, { type AgentEntry, type Theme } from "./components/Sidebar";
import Desk, { type Sheet } from "./components/Desk";
import Rail, { type TrayItem } from "./components/Rail";
import Drawer from "./components/Drawer";
import DataView from "./components/DataView";
import Inbox from "./components/Inbox";
import JobsView from "./components/JobsView";
import History from "./components/History";
import { setToolLabels, toUiEvent } from "./components/EventList";
import type { Health, View, WorkspaceAgent } from "./types";

const THEME_KEY = "harness.theme";
function readTheme(): Theme { const q = new URLSearchParams(location.search).get("theme"); if (q === "light" || q === "dark") return q; try { const t = localStorage.getItem(THEME_KEY); if (t === "light" || t === "dark") return t; } catch { /* ignore */ } return "auto"; }

export default function App() {
  const [base, setBaseState] = useState(getBase());
  const [home, setHome] = useState<Health | null>(null);           // 이 화면을 서빙한 비서(홈). 작업 공간 목록의 기준.
  const [agents, setAgents] = useState<WorkspaceAgent[]>([]);
  const [health, setHealth] = useState<Health | null>(null);        // 지금 보고 있는 비서
  const [down, setDown] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [authErr, setAuthErr] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Record<string, string>>({});
  const sessionsRef = useRef(sessions); sessionsRef.current = sessions;
  const [sheets, setSheets] = useState<Sheet[]>([]);
  const [tray, setTray] = useState<TrayItem[]>([]);
  const [selectedRun, setSelectedRunState] = useState<string | null>(() => new URLSearchParams(location.search).get("run"));
  const [refreshKey, setRefreshKey] = useState(0);
  const [theme, setThemeState] = useState<Theme>(readTheme);
  const [railHidden, setRailHidden] = useState(false);
  const [view, setViewState] = useState<View>(() => { const v = new URLSearchParams(location.search).get("view"); return (["desk", "inbox", "jobs", "history", "data"] as string[]).includes(v ?? "") ? (v as View) : "desk"; });
  const setView = useCallback((v: View) => { setViewState(v); const u = new URL(location.href); v === "desk" ? u.searchParams.delete("view") : u.searchParams.set("view", v); history.replaceState(null, "", u); }, []);

  const setSelectedRun = useCallback((id: string | null) => {
    setSelectedRunState(id);
    const u = new URL(location.href); id ? u.searchParams.set("run", id) : u.searchParams.delete("run"); history.replaceState(null, "", u);
  }, []);
  const pick = useCallback((b: string) => { setBase(b); setBaseState(b); setSelectedRun(null); setHealth(null); }, [setSelectedRun]);
  const setTheme = useCallback((t: Theme) => { setThemeState(t); try { t === "auto" ? localStorage.removeItem(THEME_KEY) : localStorage.setItem(THEME_KEY, t); } catch { /* ignore */ } }, []);
  useEffect(() => { if (theme === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = theme; }, [theme]);

  // 홈 비서의 작업 공간(다른 비서 목록)은 30초마다, 지금 비서의 상태는 5초마다.
  useEffect(() => {
    const homeApi = apiAt("");
    const load = async () => {
      try { const [h, w] = await Promise.all([homeApi.health(), homeApi.workspace()]); setHome(h); setAgents(w.agents);
        // 결재함 전표는 다른 비서의 도구 이름을 쓰므로 모든 비서의 라벨을 미리 모아 둔다
        await Promise.all(w.agents.filter((a) => a.ok).map(async (a) => { try { setToolLabels((await apiAt(a.url).health()).pack?.tool_labels); } catch { /* 그 비서 다운 */ } }));
      } catch { /* 홈 다운 */ }
    };
    load(); const t = setInterval(load, 30000); return () => clearInterval(t);
  }, []);
  const refreshHealth = useCallback(async () => {
    try { const h = await api.health(); setToolLabels(h.pack?.tool_labels); setHealth(h); setDown(false); } catch { setDown(true); }
    try { setMe(await api.me()); setAuthErr(null); } catch (e) { setMe(null); setAuthErr((e as Error).message); }
  }, []);
  useEffect(() => { refreshHealth(); const t = setInterval(refreshHealth, 5000); return () => clearInterval(t); }, [refreshHealth, base]);
  useEffect(() => { document.title = health?.pack?.title ? `${health.pack.title} · 업무 비서` : "업무 비서"; }, [health?.pack?.title]);

  // 비서 목록 = 홈 + 작업 공간. 결재함은 전부에서 모아 온다.
  const bases = useMemo(() => ["", ...agents.filter((a) => a.ok).map((a) => a.url)], [agents]);
  const nameOf = useCallback((b: string) => (b === "" ? home?.pack?.title ?? "비서" : agents.find((a) => a.url === b)?.name ?? b), [home, agents]);
  const loadTray = useCallback(async () => {
    const all = await Promise.all(bases.map(async (b) => { try { return (await apiAt(b).approvals("pending")).map((a) => ({ base: b, agent: nameOf(b), a })); } catch { return [] as TrayItem[]; } }));
    setTray(all.flat().sort((x, y) => x.a.created_at.localeCompare(y.a.created_at)));
  }, [bases, nameOf]);
  useEffect(() => { loadTray(); const t = setInterval(loadTray, 3000); return () => clearInterval(t); }, [loadTray]);

  const entries: AgentEntry[] = useMemo(() => [
    { base: "", name: nameOf(""), pack: home?.pack?.name ?? null, description: home?.pack?.description ?? "", ok: !!home, pending: tray.filter((t) => t.base === "").length },
    ...agents.map((a) => ({ base: a.url, name: a.name ?? a.url, pack: a.pack, description: a.description, ok: a.ok, pending: tray.filter((t) => t.base === a.url).length })),
  ], [home, agents, tray, nameOf]);

  const ensureSession = useCallback(async (b: string) => {
    const have = sessionsRef.current[b]; if (have) return have;
    const s = await apiAt(b).createSession(); setSessions((m) => ({ ...m, [b]: s.id })); sessionsRef.current = { ...sessionsRef.current, [b]: s.id }; return s.id;
  }, []);
  /** 기록·맡긴 작업에서 고른 실행을 저장된 이벤트로 작업지 한 장으로 되살린다. */
  const openRunAsSheet = useCallback(async (runId: string) => {
    const b = getBase();
    if (sheets.some((s) => s.runId === runId && s.base === b)) { setView("desk"); return; }
    try {
      const r = await apiAt(b).run(runId);
      let seq = 0;
      const events = r.events.map((e) => toUiEvent(++seq, e.kind, e.data)).filter((e): e is NonNullable<typeof e> => e !== null);
      const pending = r.approvals.map((a) => ({ approval_id: a.id, tool: a.tool, arguments: a.args, tier: a.tier, run_id: a.run_id }));
      const decided = Object.fromEntries(r.approvals.filter((a) => a.status !== "pending").map((a) => [a.id, a.status === "approved" ? "approve" : "deny"] as const));
      const meta = r.latency_s != null ? `${r.latency_s}초 · 토큰 ${((r.prompt_tokens ?? 0) + (r.completion_tokens ?? 0)).toLocaleString("ko-KR")}` : "";
      setSheets((ss) => [{ id: -Date.now(), base: b, agent: nameOf(b), question: r.question, events, pending, decided, answer: r.answer ?? r.error ?? "(답 없음)", draft: "", meta, error: null, runId, at: r.created_at }, ...ss]);  // 저장소 시각은 로컬 시간이라 그대로 쓴다
      setView("desk");
    } catch (e) { setSelectedRun(runId); console.warn(e); }
  }, [sheets, nameOf, setView, setSelectedRun]);
  const onRunFinished = useCallback(() => { setRefreshKey((k) => k + 1); refreshHealth(); loadTray(); }, [refreshHealth, loadTray]);  // 드로어는 작업지의 "실행 상세" 로만 연다

  const agentName = health?.pack?.title ?? nameOf(base);
  const modelOff = !!health?.model && !health.model.hosted && !health.model.reachable;
  const canChat = (me?.can.chat ?? false) && !modelOff && !down;
  const blockedWhy = down ? "이 비서 서비스에 연결되지 않았습니다. 서비스가 켜져 있는지 확인하세요."
    : authErr && health ? "이 비서는 API 키가 필요합니다. 왼쪽 아래에서 운영자나 결재자 키로 로그인하세요."
    : modelOff ? "모델 서버가 꺼져 있어 질문할 수 없습니다. 터미널에서 scripts/serve_llm.sh 를 실행하면 10초 안에 다시 연결됩니다." : null;

  return (
    <div className={`shell ${railHidden || view !== "desk" ? "no-rail" : "show-rail"}`}>
      <Sidebar agents={entries} current={base} onPick={pick} view={view} onView={setView} pendingTotal={tray.length} health={health} down={down} me={me} onAuthChange={refreshHealth} theme={theme} onTheme={setTheme} railHidden={railHidden} onToggleRail={() => setRailHidden((v) => !v)} />
      {view === "desk" && <Desk base={base} agentName={agentName} welcome={health?.pack?.welcome ?? "도구로 확인해 답하고, 바꾸는 일은 결재를 받은 뒤에만 합니다."} examples={health?.pack?.examples ?? []}
            emptyHint={health?.pack?.empty_hint ?? "질문을 보내면 답과 근거를 한 장의 작업지로 정리합니다."} sheets={sheets} setSheets={setSheets} ensureSession={ensureSession}
            onRunFinished={onRunFinished} onOpenRun={setSelectedRun} canApprove={me?.can.approve ?? false} canChat={canChat} blockedWhy={blockedWhy} onData={health?.pack?.dashboard ? () => setView("data") : undefined} />}
      {view === "inbox" && <Inbox bases={bases} nameOf={nameOf} tray={tray} onDecided={() => { loadTray(); refreshHealth(); }} canApprove={me?.can.approve ?? false} />}
      {view === "jobs" && <JobsView base={base} agentName={agentName} sessionId={sessions[base] ?? null} ensureSession={ensureSession} onOpen={openRunAsSheet} />}
      {view === "history" && <History base={base} agentName={agentName} onOpen={openRunAsSheet} onDetail={setSelectedRun} />}
      {view === "data" && <DataView base={base} agentName={agentName} />}
      {view === "desk" && !railHidden && <Rail base={base} tray={tray} onDecided={() => { loadTray(); refreshHealth(); }} canApprove={me?.can.approve ?? false} sessionId={sessions[base] ?? null} ensureSession={ensureSession} selected={selectedRun} onSelect={setSelectedRun} refreshKey={refreshKey} />}
      {selectedRun && <Drawer base={base} runId={selectedRun} refreshKey={refreshKey} onClose={() => setSelectedRun(null)} />}
    </div>
  );
}
