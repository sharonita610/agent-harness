import { useState } from "react";
import type { SeriesData } from "../types";
import { Table } from "./ResultCard";

/** 계열 색은 고정 순서(청록·파랑·주황·보라). 필터로 계열 수가 바뀌어도 같은 이름은 같은 색이어야 하므로 이름이 아니라 자리로 준다. */
export const SERIES_COLORS = ["#0f6e66", "#2a78d6", "#eb6834", "#6b4c9a"];
const W = 640, H = 220, L = 46, R = 16, T = 14, B = 30;
const fmt = (v: number | null | undefined, unit = "") => (v === null || v === undefined ? "값 없음" : `${Number.isInteger(v) ? v.toLocaleString("ko-KR") : v.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}${unit ? " " + unit : ""}`);

function ticks(max: number): number[] {
  if (max <= 0) return [0];
  const raw = max / 4; const p = Math.pow(10, Math.floor(Math.log10(raw))); const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? p;
  const out: number[] = []; for (let v = 0; v <= max + 1e-9; v += step) out.push(+v.toFixed(6)); return out;
}

/** 선·막대 그래프. 라이브러리 없이 SVG. 축 하나, 얇은 마크, 계열이 둘 이상이면 범례와 끝 라벨, 호버하면 그 x 의 값 전부. "표로 보기" 로 같은 데이터를 표로. */
export default function Chart({ kind, data, height = H }: { kind: "line" | "bar"; data: SeriesData; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const n = data.x.length; const series = data.series.slice(0, SERIES_COLORS.length);
  const unit = data.unit ?? "";
  const vals = series.flatMap((s) => s.values).filter((v): v is number => typeof v === "number");
  const max = Math.max(0, ...vals); const tk = ticks(max); const yMax = tk[tk.length - 1] || 1;
  const right = series.length > 1 ? R + 64 : R;
  const px = (i: number) => (n <= 1 ? L + (W - L - right) / 2 : L + ((W - L - right) * i) / (n - 1));
  const py = (v: number) => T + (height - T - B) * (1 - v / yMax);
  const labelEvery = Math.max(1, Math.ceil(n / 8));
  if (!n || !series.length) return <p className="muted small">표시할 값이 없습니다.</p>;
  const rows = data.x.map((x, i) => Object.fromEntries([["x", x], ...series.map((s) => [s.name, s.values[i]])]));
  // 선 끝 라벨이 겹치지 않게: 마지막 값 순으로 정렬해 12px 이상 벌린다
  const lastOf = (s: SeriesData["series"][number]) => s.values.map((v, i) => (v === null || v === undefined ? -1 : i)).filter((i) => i >= 0).pop();
  const labelY: Record<number, number> = {};
  if (kind === "line" && series.length > 1) {
    const want = series.map((s, k) => { const li = lastOf(s); return { k, y: li === undefined ? null : py(s.values[li] as number) }; }).filter((o): o is { k: number; y: number } => o.y !== null).sort((a, b) => a.y - b.y);
    let prev = -Infinity; for (const o of want) { const y = Math.max(o.y, prev + 14); labelY[o.k] = y; prev = y; }
  }
  return (
    <div className="chart">
      {series.length > 1 && <div className="legend">{series.map((s, k) => <span key={s.name}><i style={{ background: SERIES_COLORS[k] }} />{s.name}</span>)}</div>}
      {asTable ? <Table rows={rows} max={40} /> : (
        <svg viewBox={`0 0 ${W} ${height}`} width="100%" role="img" aria-label={`${kind === "line" ? "선" : "막대"} 그래프, 계열 ${series.map((s) => s.name).join(", ")}`}
             onMouseLeave={() => setHover(null)}
             onMouseMove={(e) => { const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect(); const x = ((e.clientX - r.left) / r.width) * W;
               if (kind === "bar") { const bw = (W - L - right) / n; setHover(Math.min(n - 1, Math.max(0, Math.floor((x - L) / bw)))); }
               else { let best = 0; for (let i = 1; i < n; i++) if (Math.abs(px(i) - x) < Math.abs(px(best) - x)) best = i; setHover(best); } }}>
          {tk.map((v) => <g key={v}><line x1={L} x2={W - right} y1={py(v)} y2={py(v)} stroke="currentColor" opacity={v === 0 ? 0.35 : 0.12} /><text x={L - 6} y={py(v) + 4} textAnchor="end" fontSize="11" fill="currentColor" opacity={0.7}>{fmt(v)}</text></g>)}
          {data.x.map((x, i) => (i % labelEvery === 0 || i === n - 1) && <text key={i} x={kind === "bar" ? L + ((W - L - right) / n) * (i + 0.5) : px(i)} y={height - B + 16} textAnchor="middle" fontSize="11" fill="currentColor" opacity={0.7}>{x}</text>)}
          {kind === "bar" ? series.map((s, k) => {
            const bw = (W - L - right) / n; const gap = 2; const w = Math.max(2, (bw - 8) / series.length - gap);
            return s.values.map((v, i) => v === null || v === undefined ? null : (
              <rect key={`${k}-${i}`} x={L + bw * i + 4 + k * (w + gap)} y={py(v)} width={w} height={Math.max(0, py(0) - py(v))} rx={2} fill={SERIES_COLORS[k]} opacity={hover === null || hover === i ? 1 : 0.45} />));
          }) : series.map((s, k) => {
            const pts = s.values.map((v, i) => (v === null || v === undefined ? null : `${px(i)},${py(v)}`));
            const segs: string[] = []; let cur: string[] = []; for (const p of pts) { if (p) cur.push(p); else { if (cur.length) segs.push(cur.join(" ")); cur = []; } } if (cur.length) segs.push(cur.join(" "));
            const lastIdx = lastOf(s);
            return <g key={k}>
              {segs.map((d, j) => <polyline key={j} points={d} fill="none" stroke={SERIES_COLORS[k]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />)}
              {s.values.map((v, i) => v === null || v === undefined ? null : <circle key={i} cx={px(i)} cy={py(v)} r={hover === i ? 5 : 3.5} fill={SERIES_COLORS[k]} stroke="var(--sheet)" strokeWidth={1.5} />)}
            </g>;
          })}
          {kind === "line" && series.length > 1 && series.map((s, k) => { const li = lastOf(s); return li === undefined ? null : <text key={`l${k}`} x={px(li) + 8} y={(labelY[k] ?? py(s.values[li] as number)) + 4} fontSize="11" fill={SERIES_COLORS[k]} paintOrder="stroke" stroke="var(--sheet)" strokeWidth={3}>{s.name}</text>; })}
          {hover !== null && (() => { const x = kind === "bar" ? L + ((W - L - right) / n) * (hover + 0.5) : px(hover); const lines = [data.x[hover], ...series.map((s) => `${series.length > 1 ? s.name + " " : ""}${fmt(s.values[hover], unit)}`)];
            const bw = Math.max(...lines.map((l) => l.length)) * 6.6 + 16; const bx = Math.min(Math.max(L, x - bw / 2), W - right - bw); const by = T;
            return <g pointerEvents="none"><line x1={x} x2={x} y1={T} y2={height - B} stroke="currentColor" opacity={0.25} strokeDasharray="3 3" />
              <rect x={bx} y={by} width={bw} height={14 * lines.length + 8} rx={4} fill="var(--sheet)" stroke="var(--rule-strong)" />
              {lines.map((l, i) => <text key={i} x={bx + 8} y={by + 14 * (i + 1)} fontSize="11" fill="currentColor" fontWeight={i === 0 ? 600 : 400}>{l}</text>)}</g>; })()}
        </svg>
      )}
      <div className="chart-foot"><span className="muted small">{unit ? `단위 ${unit}` : ""}</span><button className="ghost small" onClick={() => setAsTable((v) => !v)}>{asTable ? "그래프로 보기" : "표로 보기"}</button></div>
    </div>
  );
}
