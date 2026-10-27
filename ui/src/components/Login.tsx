import { useState } from "react";
import { setApiKey } from "../api";
import type { Me } from "../api";

/** 키 입력 한 줄. 개발 모드면 그 사실만 보여준다. */
export default function Login({ me, onChange }: { me: Me | null; onChange: () => void }) {
  const [key, setKey] = useState("");
  const [show, setShow] = useState(false);
  const roleKo: Record<string, string> = { viewer: "조회", operator: "운영자", approver: "결재자", admin: "관리자" };
  if (me && !me.anonymous && !show) {
    return <span className="login"><b>{me.name}</b> ({roleKo[me.role] ?? me.role}{me.scope_text ? `, 범위 ${me.scope_text}` : ""}) <button className="ghost small" onClick={() => { setApiKey(null); onChange(); }}>로그아웃</button></span>;
  }
  if (me?.anonymous && !show) {
    return <span className="login muted">개발 모드, 인증 없음 <button className="ghost small" onClick={() => setShow(true)}>키로 로그인</button></span>;
  }
  return (
    <form className="login" onSubmit={(e) => { e.preventDefault(); if (key.trim()) { setApiKey(key.trim()); setKey(""); setShow(false); onChange(); } }}>
      <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="API 키" aria-label="API 키" autoComplete="off" />
      <button className="primary small" type="submit" disabled={!key.trim()}>로그인</button>
      {me?.anonymous && <button className="ghost small" type="button" onClick={() => setShow(false)}>닫기</button>}
    </form>
  );
}
