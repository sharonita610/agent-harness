import type { Me } from "../api";
import type { Health } from "../types";
import Login from "./Login";

export interface AgentEntry { base: string; name: string; pack: string | null; description: string; ok: boolean; pending: number }
export type Theme = "auto" | "light" | "dark";

interface Props { agents: AgentEntry[]; current: string; onPick: (base: string) => void; health: Health | null; down: boolean; me: Me | null; onAuthChange: () => void; theme: Theme; onTheme: (t: Theme) => void; railHidden: boolean; onToggleRail: () => void }

/** 왼쭉 사이드바: 비서 목록, 연결 상태, 로그인, 화면 설정. */
export default function Sidebar({ agents, current, onPick, health, down, me, onAuthChange, theme, onTheme, railHidden, onToggleRail }: Props) {
  const nextTheme: Record<Theme, Theme> = { auto: "light", light: "dark", dark: "auto" };
  const themeKo: Record<Theme, string> = { auto: "화면 자동", light: "화면 밝게", dark: "화면 어둡게" };
  return (
    <nav className="side" aria-label="비서 선택">
      <div className="brand">업무 비서<small>묻고, 확인하고, 결재합니다</small></div>
      <div>
        <h4>비서</h4>
        <div className="agents">
          {agents.map((a) => (
            <button key={a.base || "self"} className={`agent ${a.base === current ? "on" : ""}`} onClick={() => onPick(a.base)} title={a.description || a.name} aria-current={a.base === current ? "page" : undefined}>
              <i className={`dot ${a.ok ? "" : "off"}`} aria-hidden="true" /><span>{a.name}</span>{a.pending > 0 ? <span className="n" aria-label={`결재 ${a.pending}건`}>{a.pending}</span> : <span />}
            </button>
          ))}
        </div>
      </div>
      <div className="foot">
        <div className="status">
          <span>{down ? <span className="st-failed">연결되지 않음</span> : health ? <>도구 <b>{health.tools}개</b></> : "연결 확인 중"}</span>
          {health && <span>모델 <b>{health.provider}</b>{health.model && !health.model.hosted && (health.model.reachable ? <span className="st-ok"> 켜짐</span> : <span className="st-failed"> 꺼짐</span>)}</span>}
        </div>
        <Login me={me} onChange={onAuthChange} />
        <div className="links">
          <button className="ghost small" onClick={() => onTheme(nextTheme[theme])}>{themeKo[theme]}</button>
          <button className="ghost small" onClick={onToggleRail}>{railHidden ? "결재함 보기" : "결재함 숨기기"}</button>
          <a href={(current || "") + "/docs"} target="_blank" rel="noreferrer">API 문서</a>
        </div>
      </div>
    </nav>
  );
}
