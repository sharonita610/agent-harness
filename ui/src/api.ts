import type { Approval, Health, Run, RunEvent, Session, Tool, Trace } from "./types";

const KEY_STORAGE = "harness.apiKey";
export function getApiKey(): string | null { try { return localStorage.getItem(KEY_STORAGE); } catch { return null; } }
export function setApiKey(key: string | null): void { try { key ? localStorage.setItem(KEY_STORAGE, key) : localStorage.removeItem(KEY_STORAGE); } catch { /* 저장 불가 환경 */ } }
export function authHeaders(): Record<string, string> { const k = getApiKey(); return k ? { Authorization: `Bearer ${k}` } : {}; }

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { ...init, headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers as Record<string, string> | undefined) } });
  if (!r.ok) {
    let detail = "";
    try { detail = ((await r.json()) as { detail?: string }).detail ?? ""; } catch { /* 본문 없음 */ }
    throw new ApiError(r.status, detail || `${init?.method ?? "GET"} ${url} → HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}

export interface Me { name: string; role: string; anonymous: boolean; auth_required: boolean; scope?: Record<string, unknown> | null; scope_text?: string | null; can: { chat: boolean; approve: boolean; admin: boolean } }
export interface AuditEntry { id: number; ts: string; user: string; role: string; action: string; target: string; detail: Record<string, unknown> }

export const api = {
  health: () => j<Health>("/health"),
  me: () => j<Me>("/me"),
  audit: (limit = 50) => j<AuditEntry[]>(`/audit?limit=${limit}`),
  tools: () => j<Tool[]>("/tools"),
  createSession: () => j<Session>("/sessions", { method: "POST", body: "{}" }),
  session: (id: string) => j<Session>(`/sessions/${id}`),
  approvals: (status = "pending") => j<Approval[]>(`/approvals?status=${status}`),
  decide: (id: string, decision: "approve" | "deny") => j<Approval>(`/approvals/${id}`, { method: "POST", body: JSON.stringify({ decision, by: "web" }) }),
  submitJob: (question: string, session_id?: string) => j<{ run_id: string }>("/jobs", { method: "POST", body: JSON.stringify({ question, session_id }) }),
  jobs: (limit = 20) => j<Run[]>(`/jobs?limit=${limit}`),
  cancelJob: (id: string) => j<Run>(`/jobs/${id}/cancel`, { method: "POST" }),
  runs: (limit = 30) => j<Run[]>(`/runs?limit=${limit}`),
  run: (id: string) => j<Run & { events: RunEvent[]; approvals: Approval[] }>(`/runs/${id}`),
  trace: (id: string) => j<Trace>(`/runs/${id}/trace`),
};

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
  const r = await fetch("/chat", { method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() }, body: JSON.stringify(body), signal });
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
