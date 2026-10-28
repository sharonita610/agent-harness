import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { parseSSEChunk } from "./api";
import { layoutSpans } from "./components/TraceView";
import { briefArgs, setToolLabels, toUiEvent } from "./components/EventList";
import ResultCard from "./components/ResultCard";
import Rich from "./components/Rich";
import Chart, { yTicks } from "./components/Chart";
import { HeatGrid, Stats } from "./components/DataView";
import QueueBoard from "./components/QueueBoard";
import type { Span } from "./types";

describe("parseSSEChunk", () => {
  it("parses event and json data", () => {
    expect(parseSSEChunk('event: tool_call\ndata: {"name":"run_sql","step":1}')).toEqual({
      event: "tool_call",
      data: { name: "run_sql", step: 1 },
    });
  });
  it("ignores comments and handles bad json", () => {
    expect(parseSSEChunk(": ping")).toBeNull();
    expect(parseSSEChunk("event: x\ndata: {oops")).toEqual({ event: "x", data: { raw: "{oops" } });
  });
});

describe("layoutSpans", () => {
  it("positions children relative to the root span", () => {
    const spans: Span[] = [
      {
        span_id: "r",
        parent_id: null,
        name: "agent.run",
        kind: "run",
        start: 100,
        end: 110,
        duration_ms: 10000,
        status: "ok",
        attrs: {},
      },
      {
        span_id: "t",
        parent_id: "r",
        name: "tool.run_sql",
        kind: "tool",
        start: 102,
        end: 104,
        duration_ms: 2000,
        status: "ok",
        attrs: {},
      },
    ];
    const rows = layoutSpans(spans);
    expect(rows[1]).toMatchObject({ leftPct: 20, widthPct: 20, depth: 1 });
    expect(rows[0].widthPct).toBe(100);
  });
});

describe("toUiEvent", () => {
  it("shows a plot when a tool result carries an image url, uses pack labels, and keeps the raw result", () => {
    setToolLabels({ run_simulation: "시뮬레이션 실행" });
    const call = toUiEvent(1, "tool_call", {
      name: "run_simulation",
      arguments: { netlist: "* rc\nR1 a 0 1k", measurements: ["fc WHEN vdb(out)=-3"] },
    });
    expect(call).toMatchObject({ label: "시뮬레이션 실행", tone: "tool", tool: "run_simulation" });
    expect(call?.detail.split("\n")[0]).toBe("fc WHEN vdb(out)=-3");
    const plot = toUiEvent(2, "tool_result", {
      name: "plot_waveform",
      preview: "{}",
      url: "/files/spice/swp_1/plot.svg",
    });
    expect(plot).toMatchObject({
      label: "그래프",
      image: "/files/spice/swp_1/plot.svg",
      tool: "plot_waveform",
    });
    const sql = toUiEvent(3, "tool_result", {
      name: "run_sql",
      preview: "{}",
      result: { columns: ["a"], rows: [{ a: 1 }] },
    });
    expect(sql?.image).toBeUndefined();
    expect(sql?.result).toEqual({ columns: ["a"], rows: [{ a: 1 }] });
  });
  it("summarises what was sent per tool", () => {
    expect(briefArgs("delegate", { agent: "spice", question: "RC 차단 주파수" })).toBe(
      "spice 비서에게: RC 차단 주파수",
    );
    expect(briefArgs("run_sql", { sql: "SELECT 1" })).toBe("SELECT 1");
  });
});

describe("Rich", () => {
  const html = (text: string) => renderToStaticMarkup(createElement(Rich, { text }));
  it("renders markdown tables, bullets, bold and italics", () => {
    const h = html("결과\n| R1 | fc |\n|---|---|\n| 1 kΩ | 1.59 kHz |\n- 첫째\n- **둘째**\n*조건* 끝");
    expect(h).toContain('<table class="md"');
    expect(h).toMatch(/<th>(<span>)?R1(<\/span>)?<\/th>/);
    expect(h).toContain("1.59 kHz");
    expect(h).toMatch(
      /<ul class="md"><li>(<span>)?첫째(<\/span>)?<\/li><li>(<span><\/span>)?<strong>둘째<\/strong>/,
    );
    expect(h).toContain("<em>조건</em>");
  });
});

describe("ResultCard", () => {
  const html = (result: unknown, tool?: string) =>
    renderToStaticMarkup(createElement(ResultCard, { result, tool }));
  it("renders query rows as a table with korean headers", () => {
    const h = html({
      columns: ["line", "ng_rate_pct"],
      rows: [{ line: 2, ng_rate_pct: 3.456 }],
      row_count: 1,
      truncated: false,
    });
    expect(h).toContain("<table");
    expect(h).toContain("NG율(%)");
    expect(h).toContain("3.456");
    expect(h).toContain("행 수");
    expect(h).not.toContain("truncated");
  });
  it("renders measurements as big numbers and a plot when the result has an image url", () => {
    const h = html({
      run_id: "run_1",
      measurements_readable: { fc: "1.59 kHz" },
      url: "/files/spice/run_1/plot.svg",
    });
    expect(h).toContain("1.59 kHz");
    expect(h).toContain("<img");
  });
  it("renders search hits as a source list and delegate answers as a quote", () => {
    expect(html([{ source: "규정 > 환불", snippet: "7일 안에", score: 0.9 }])).toContain("규정 &gt; 환불");
    const inDict = html({
      query: "환불",
      mode: "bm25",
      hits: [{ doc: "규정", section: "환불", text: "7일 안에 환불", score: 0.9, source: "규정 > 환불" }],
    });
    expect(inDict).toContain('<ul class="sources"');
    expect(inDict).not.toContain("<table");
    expect(
      html({ agent: "spice", state: "completed", answer: "1.59 kHz 입니다", task_id: "t1" }, "delegate"),
    ).toContain("spice 비서의 답");
  });
  it("falls back to collapsed raw json for unknown shapes", () => {
    expect(
      html([
        [1, 2],
        [3, 4],
      ]),
    ).toContain("원문 보기");
  });
});

describe("data tab", () => {
  it("draws a line chart with one polyline per series, a legend, end labels and few x labels", () => {
    const h = renderToStaticMarkup(
      createElement(Chart, {
        kind: "line",
        data: {
          x: Array.from({ length: 14 }, (_, i) => `10-${i + 14}`),
          series: [
            { name: "1라인", values: Array(14).fill(1.2) },
            { name: "2라인", values: [1, 2, null, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14] },
          ],
          unit: "%",
        },
      }),
    );
    expect((h.match(/<polyline/g) ?? []).length).toBe(3); // 2라인은 빈 값에서 끊겨 두 토막
    expect(h).toContain('class="legend"');
    expect(h).toContain(">2라인</text>");
    expect((h.match(/y="206"/g) ?? []).length).toBeLessThanOrEqual(8); // x 라벨은 8개 이하
    expect(h).toContain("표로 보기");
  });
  it("draws grouped bars and renders stats and heat cells", () => {
    const bars = renderToStaticMarkup(
      createElement(Chart, { kind: "bar", data: { x: ["a", "b"], series: [{ name: "n", values: [3, 5] }] } }),
    );
    expect((bars.match(/<rect/g) ?? []).length).toBe(2);
    const st = renderToStaticMarkup(
      createElement(Stats, {
        data: [
          { label: "NG율", value: 2.345, unit: "%", tone: "bad" },
          { label: "연결", value: "3/3" },
        ],
      }),
    );
    expect(st).toContain("2.35");
    expect(st).toContain("stat bad");
    const heat = renderToStaticMarkup(
      createElement(HeatGrid, {
        data: { rows: ["1라인"], cols: ["코팅", "권취"], values: [[0.5, 2.0]], unit: "%" },
      }),
    );
    expect(heat).toContain("rgba(31,78,121,0.800)"); // 최댓값 칸이 가장 진하다
    expect(heat).toContain("rgba(31,78,121,0.080)"); // 최솟값 칸이 가장 옅다
    expect(heat).toContain("color:#fff");
  });
});

describe("QueueBoard", () => {
  it("draws queued as dashed, running with a progress fill, done by duration, and ranks the queue", () => {
    const base = "2026-10-29T03:00:00";
    const jobs = [
      {
        id: "r1",
        session_id: "s",
        kind: "job",
        question: "스윕 A",
        status: "running",
        answer: null,
        stopped_reason: null,
        steps: null,
        latency_s: null,
        prompt_tokens: null,
        completion_tokens: null,
        error: null,
        created_at: base,
        started_at: "2026-10-29T03:00:10",
        finished_at: null,
      },
      {
        id: "q1",
        session_id: "s",
        kind: "job",
        question: "스윕 B",
        status: "queued",
        answer: null,
        stopped_reason: null,
        steps: null,
        latency_s: null,
        prompt_tokens: null,
        completion_tokens: null,
        error: null,
        created_at: "2026-10-29T03:00:20",
        started_at: null,
        finished_at: null,
      },
      {
        id: "d1",
        session_id: "s",
        kind: "job",
        question: "집계",
        status: "done",
        answer: "x",
        stopped_reason: null,
        steps: 2,
        latency_s: 20,
        prompt_tokens: 1,
        completion_tokens: 1,
        error: null,
        created_at: "2026-10-29T02:58:00",
        started_at: "2026-10-29T02:58:00",
        finished_at: "2026-10-29T02:58:20",
      },
    ] as const;
    const now = new Date("2026-10-29T03:01:00").getTime(); // 저장소 시각처럼 로컬 시간
    const h = renderToStaticMarkup(
      createElement(QueueBoard, {
        jobs: jobs as unknown as import("./types").Run[],
        progress: { r1: { name: "run_sweep", done: 3, total: 7, label: "R1=1 kΩ", at: "" } },
        now,
        onOpen: () => {},
      }),
    );
    expect(h).toContain("qbar queued");
    expect(h).toContain("1번째 대기");
    expect(h).toContain("qfill");
    expect(h).toMatch(/width:42\.8\d+%/); // 3/7
    expect(h).toContain("3/7 · R1=1 kΩ");
    expect(h).toContain("완료 · 20초");
    expect(h).not.toContain("left:100%"); // 시각을 미래로 읽어 오른쪽 끝에 몰리지 않는다
  });
});

describe("yTicks", () => {
  it("keeps zero for bars and for wide ranges, but zooms a narrow line range", () => {
    expect(yTicks("bar", [0.9, 1.2])[0]).toBe(0);
    expect(yTicks("line", [0.2, 1.2])[0]).toBe(0);
    const t = yTicks("line", [0.95, 1.05, 1.21, 1.1]);
    expect(t[0]).toBeGreaterThan(0.7);
    expect(t[0]).toBeLessThanOrEqual(0.95);
    expect(t[t.length - 1]).toBeGreaterThanOrEqual(1.21);
    expect(t.length).toBeGreaterThanOrEqual(3);
  });
});
