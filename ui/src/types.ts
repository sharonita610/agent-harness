export type Tier = "read" | "write" | "destructive";

export interface Health { status: string; tools: number; provider: string; pending_approvals: number; auth_required?: boolean; model?: { provider: string; reachable: boolean; hosted: boolean } }
export interface Tool { name: string; tier: Tier; policy: string; description: string }
export interface Session { id: string; created_at: string; provider: string; title: string | null; messages: { role: string; content: string; created_at: string }[] }
export interface Run {
  id: string; session_id: string; kind: "chat" | "job"; question: string; status: string; answer: string | null;
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

/** 채팅 스트림에서 화면에 쌓는 한 줄 */
export interface UiEvent { id: number; kind: string; label: string; detail: string; tone: "" | "tool" | "ok" | "err" | "warn"; approvalId?: string }
