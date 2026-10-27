import { describe, expect, it } from "vitest";
import { parseSSEChunk } from "./api";
import { layoutSpans } from "./components/TraceView";
import { setToolLabels, toUiEvent } from "./components/EventList";
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

describe("toUiEvent", () => {
  it("shows a plot when a tool result carries an image url, and uses pack labels", () => {
    setToolLabels({ run_simulation: "시뮬레이션 실행" });
    const call = toUiEvent(1, "tool_call", { name: "run_simulation", arguments: { netlist: "* rc\nR1 a 0 1k", measurements: ["fc WHEN vdb(out)=-3"] } });
    expect(call).toMatchObject({ label: "시뮬레이션 실행", tone: "tool" });
    expect(call?.detail.split("\n")[0]).toBe("fc WHEN vdb(out)=-3");
    const plot = toUiEvent(2, "tool_result", { name: "plot_waveform", preview: "{}", url: "/files/spice/swp_1/plot.svg" });
    expect(plot).toMatchObject({ label: "그래프", image: "/files/spice/swp_1/plot.svg" });
    expect(toUiEvent(3, "tool_result", { name: "run_sql", preview: "{}" })?.image).toBeUndefined();
  });
});
