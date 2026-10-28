export type Tier = "read" | "write" | "destructive";

export interface Pack { name: string; title: string; description: string; welcome: string; examples: string[][]; empty_hint: string; tool_labels: Record<string, string> }
export interface Health { status: string; tools: number; provider: string; pending_approvals: number; auth_required?: boolean; model?: { provider: string; reachable: boolean; hosted: boolean }; pack?: Pack }
export interface WorkspaceAgent { url: string; name: string | null; pack: string | null; description: string; ok: boolean; error?: string }
export interface Workspace { self: { pack: string; name: string; description: string }; agents: WorkspaceAgent[] }
export interface Tool { name: string; tier: Tier; policy: string; description: string; label?: string }
export interface Session { id: string; created_at: string; provider: string; title: string | null; messages: { role: string; content: string; created_at: string }[] }
export interface Run {
  id: string; session_id: string; kind: "chat" | "job" | "a2a"; question: string; status: string; answer: string | null;
  stopped_reason: string | null; steps: number | null; latency_s: number | null; prompt_tokens: number | null; completion_tokens: number | null;
  error: string | null; created_at: string; started_at: string | null; finished_at: string | null;
}
export interface RunEvent { seq: number; kind: string; step: number | null; data: Record<string, unknown>; ts: string }
export interface Approval { id: string; run_id: string; tool: string; args: Record<string, unknown>; tier: Tier; status: string; created_at: string; decided_at: string | null; decided_by: string | null }
export interface Span {
  span_id: string; parent_id: string | null; name: string; kind: "run" | "llm" | "tool" | "approval" | "nudge";
  start: number; end: number | null; duration_ms: number | null; status: string; attrs: Record<string, unknown>;
}
export interface Trace { summary: { spans: number; llm_calls: number; tool_calls: number; tool_errors: number; approvals?: number; llm_ms: number; tool_ms: number; human_ms?: number; total_ms: number }; spans: Span[] }

/** 화면에 쌓는 한 줄. label 은 사람 말, detail 은 접힌 원문, result 는 도구 결과 원본(카드로 그린다). */
export interface UiEvent { id: number; kind: string; label: string; detail: string; tone: "" | "tool" | "ok" | "err" | "warn"; approvalId?: string; image?: string; result?: unknown; tool?: string; args?: Record<string, unknown> }
