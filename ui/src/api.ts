import type { Approval, Health, Run, RunEvent, Session, Tool, Trace, Workspace } from "./types";

const KEY_STORAGE = "harness.apiKey";
const BASE_STORAGE = "harness.base";
export function getApiKey(): string | null { try { return localStorage.getItem(KEY_STORAGE); } catch { return null; } }
export function setApiKey(key: string | null): void { try { key ? localStorage.setItem(KEY_STORAGE, key) : localStorage.removeItem(KEY_STORAGE); } catch { /* 저장 불가 환경 */ } }
export function authHeaders(): Record<string, string> { const k = getApiKey(); return k ? { Authorization: `Bearer ${k}` } : {}; }

/** 지금 보고 있는 비서 서비스의 주소. 비어 있으면 이 화면을 서빙한 서비스(상대 경로). */
let BASE = "";
try { BASE = localStorage.getItem(BASE_STORAGE) ?? ""; } catch { /* ignore */ }
export function getBase(): string { return BASE; }
export function setBase(url: string): void { BASE = url.replace(/\/$/, ""); try { url ? localStorage.setItem(BASE_STORAGE, BASE) : localStorage.removeItem(BASE_STORAGE); } catch { /* ignore */ } }

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

async function j<T>(base: string, url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(base + url, { ...init, headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers as Record<string, string> | undefined) } });
  if (!r.ok) {
    let detail = "";
    try { detail = ((await r.json()) as { detail?: string }).detail ?? ""; } catch { /* 본문 없음 */ }
    throw new ApiError(r.status, detail || `${init?.method ?? "GET"} ${url} → HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}

export interface Me { name: string; role: string; anonymous: boolean; auth_required: boolean; pack?: string; scope?: Record<string, unknown> | null; scope_text?: string | null; can: { chat: boolean; approve: boolean; admin: boolean } }
export interface AuditEntry { id: number; ts: string; user: string; role: string; action: string; target: string; detail: Record<string, unknown> }

/** base 를 고정한 API 묶음. 다른 비서 서비스의 결재함을 볼 때 쓴다. */
export function makeApi(base: () => string) {
  return {
    health: () => j<Health>(base(), "/health"),
    me: () => j<Me>(base(), "/me"),
    workspace: () => j<Workspace>(base(), "/workspace"),
    audit: (limit = 50) => j<AuditEntry[]>(base(), `/audit?limit=${limit}`),
    tools: () => j<Tool[]>(base(), "/tools"),
    createSession: () => j<Session>(base(), "/sessions", { method: "POST", body: "{}" }),
    session: (id: string) => j<Session>(base(), `/sessions/${id}`),
    approvals: (status = "pending") => j<Approval[]>(base(), `/approvals?status=${status}`),
    decide: (id: string, decision: "approve" | "deny") => j<Approval>(base(), `/approvals/${id}`, { method: "POST", body: JSON.stringify({ decision, by: "web" }) }),
    submitJob: (question: string, session_id?: string) => j<{ run_id: string }>(base(), "/jobs", { method: "POST", body: JSON.stringify({ question, session_id }) }),
    jobs: (limit = 20) => j<Run[]>(base(), `/jobs?limit=${limit}`),
    cancelJob: (id: string) => j<Run>(base(), `/jobs/${id}/cancel`, { method: "POST" }),
    runs: (limit = 30) => j<Run[]>(base(), `/runs?limit=${limit}`),
    run: (id: string) => j<Run & { events: RunEvent[]; approvals: Approval[] }>(base(), `/runs/${id}`),
    trace: (id: string) => j<Trace>(base(), `/runs/${id}/trace`),
  };
}
export const api = makeApi(() => BASE);
export const apiAt = (url: string) => makeApi(() => url.replace(/\/$/, ""));

/** SSE 한 덩어리("event: x\ndata: {...}") → {event, data}. 주석(": ping") 은 null. */
export function parseSSEChunk(chunk: string): { event: string; data: Record<string, unknown> } | null {
  if (!chunk.trim() || chunk.startsWith(":")) return null;
  let event = "message";
  let data = "";
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  let parsed: Record<string, unknown> = {};
  try { parsed = data ? (JSON.parse(data) as Record<string, unknown>) : {}; } catch { parsed = { raw: data }; }
  return { event, data: parsed };
}

/** POST /chat 를 읽어 이벤트마다 콜백. 스트림이 끝나면 resolve. */
export async function streamChat(body: { message: string; session_id?: string }, onEvent: (event: string, data: Record<string, unknown>) => void, signal?: AbortSignal): Promise<void> {
  const r = await fetch(BASE + "/chat", { method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() }, body: JSON.stringify(body), signal });
  if (!r.ok || !r.body) {
    let detail = "";
    try { detail = ((await r.json()) as { detail?: string }).detail ?? ""; } catch { /* 본문 없음 */ }
    throw new ApiError(r.status, detail || `POST /chat → HTTP ${r.status}`);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const ev = parseSSEChunk(chunk);
      if (ev) onEvent(ev.event, ev.data);
    }
  }
}

/** 파일 URL(/files/...)을 지금 비서의 절대 주소로. */
export function fileUrl(path: string): string { return path.startsWith("http") ? path : BASE + path; }
