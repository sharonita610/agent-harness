import { describe, expect, it } from "vitest";
import { parseSSEChunk } from "./api";
import { layoutSpans } from "./components/TraceView";
import type { Span } from "./types";

describe("parseSSEChunk", () => {
  it("parses event and json data", () => {
    expect(parseSSEChunk('event: tool_call\ndata: {"name":"run_sql","step":1}')).toEqual({ event: "tool_call", data: { name: "run_sql", step: 1 } });
  });
  it("ignores comments and handles bad json", () => {
    expect(parseSSEChunk(": ping")).toBeNull();
    expect(parseSSEChunk("event: x\ndata: {oops")).toEqual({ event: "x", data: { raw: "{oops" } });
  });
});

describe("layoutSpans", () => {
  it("positions children relative to the root span", () => {
    const spans: Span[] = [
      { span_id: "r", parent_id: null, name: "agent.run", kind: "run", start: 100, end: 110, duration_ms: 10000, status: "ok", attrs: {} },
      { span_id: "t", parent_id: "r", name: "tool.run_sql", kind: "tool", start: 102, end: 104, duration_ms: 2000, status: "ok", attrs: {} },
    ];
    const rows = layoutSpans(spans);
    expect(rows[1]).toMatchObject({ leftPct: 20, widthPct: 20, depth: 1 });
    expect(rows[0].widthPct).toBe(100);
  });
});
