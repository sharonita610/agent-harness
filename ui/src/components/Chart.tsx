import { useState } from "react";
import type { SeriesData } from "../types";
import { Table } from "./ResultCard";

/** 계열 색은 고정 순서(청록·파랑·주황·보라). 필터로 계열 수가 바뀌어도 같은 이름은 같은 색이어야 하므로 이름이 아니라 자리로 준다. */
export const SERIES_COLORS = ["#1f4e79", "#1a9c8f", "#e07a2d", "#7b4fa6"]; // 남색(포인트)·청록·주황·보라
const WIDTH = 640,
  HEIGHT = 220,
  PAD_LEFT = 46,
  PAD_RIGHT = 16,
  PAD_TOP = 14,
  PAD_BOTTOM = 30;
const formatNumber = (v: number | null | undefined, unit = "") =>
  v === null || v === undefined
    ? "값 없음"
    : `${Number.isInteger(v) ? v.toLocaleString("ko-KR") : v.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}${unit ? " " + unit : ""}`;

function niceStep(span: number): number {
  const raw = Math.max(span, 1e-9) / 4;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? p;
}

/** y 축 눈금. 막대는 항상 0 에서 시작한다. 선은 값 범위가 좁으면(최대의 60% 미만) 0 을 버리고 데이터 주변으로 잡아 차이가 보이게 한다. */
export function yTicks(kind: "line" | "bar", values: number[]): number[] {
  if (!values.length) return [0, 1];
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const narrow = kind === "line" && lo > 0 && hi > 0 && (hi - lo) / hi < 0.6;
  const pad = narrow ? Math.max((hi - lo) * 0.25, hi * 0.02) : 0;
  const step = niceStep((narrow ? hi - lo + 2 * pad : Math.max(hi, 0)) || 1);
  const start = narrow ? Math.floor((lo - pad) / step) * step : 0;
  const end = Math.ceil((Math.max(hi, narrow ? hi + pad : hi) + 1e-9) / step) * step || step;
  const out: number[] = [];
  for (let v = start; v <= end + 1e-9; v += step) out.push(+v.toFixed(6));
  return out.length > 1 ? out : [start, start + step];
}

/** 선·막대 그래프. 라이브러리 없이 SVG. 축 하나, 얇은 마크, 계열이 둘 이상이면 범례와 끝 라벨, 호버하면 그 x 의 값 전부. "표로 보기" 로 같은 데이터를 표로. */
export default function Chart({
  kind,
  data,
  height: heightProp,
}: {
  kind: "line" | "bar";
  data: SeriesData;
  height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const n = data.x.length;
  const height = heightProp ?? (kind === "line" && data.series.length >= 3 ? HEIGHT + 60 : HEIGHT);
  const series = data.series.slice(0, SERIES_COLORS.length);
  const unit = data.unit ?? "";
  const vals = series.flatMap((s) => s.values).filter((v): v is number => typeof v === "number");
  const yTickValues = yTicks(kind, vals);
  const yMin = yTickValues[0];
  const yMax = yTickValues[yTickValues.length - 1];
  const ySpan = Math.max(yMax - yMin, 1e-9);
  const right = series.length > 1 ? PAD_RIGHT + 64 : PAD_RIGHT;
  const xAt = (i: number) =>
    n <= 1
      ? PAD_LEFT + (WIDTH - PAD_LEFT - right) / 2
      : PAD_LEFT + ((WIDTH - PAD_LEFT - right) * i) / (n - 1);
  const yAt = (v: number) => PAD_TOP + (height - PAD_TOP - PAD_BOTTOM) * (1 - (v - yMin) / ySpan);
  const labelEvery = Math.max(1, Math.ceil(n / 8));
  if (!n || !series.length) return <p className="muted small">표시할 값이 없습니다.</p>;
  const rows = data.x.map((x, i) =>
    Object.fromEntries([["x", x], ...series.map((s) => [s.name, s.values[i]])]),
  );
  // 선 끝 라벨이 겹치지 않게: 마지막 값 순으로 정렬해 12px 이상 벌린다
  const lastOf = (s: SeriesData["series"][number]) =>
    s.values
      .map((v, i) => (v === null || v === undefined ? -1 : i))
      .filter((i) => i >= 0)
      .pop();
  const labelY: Record<number, number> = {};
  if (kind === "line" && series.length > 1) {
    const want = series
      .map((s, k) => {
        const lastIndex = lastOf(s);
        return { k, y: lastIndex === undefined ? null : yAt(s.values[lastIndex] as number) };
      })
      .filter((o): o is { k: number; y: number } => o.y !== null)
      .sort((a, b) => a.y - b.y);
    let prev = -Infinity;
    for (const o of want) {
      const y = Math.max(o.y, prev + 14);
      labelY[o.k] = y;
      prev = y;
    }
  }
  return (
    <div className="chart">
      {series.length > 1 && (
        <div className="legend">
          {series.map((s, k) => (
            <span key={s.name}>
              <i style={{ background: SERIES_COLORS[k] }} />
              {s.name}
            </span>
          ))}
        </div>
      )}
      {asTable ? (
        <Table rows={rows} max={40} />
      ) : (
        <svg
          viewBox={`0 0 ${WIDTH} ${height}`}
          width="100%"
          role="img"
          aria-label={`${kind === "line" ? "선" : "막대"} 그래프, 계열 ${series.map((s) => s.name).join(", ")}`}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
            const x = ((e.clientX - r.left) / r.width) * WIDTH;
            if (kind === "bar") {
              const barWidth = (WIDTH - PAD_LEFT - right) / n;
              setHover(Math.min(n - 1, Math.max(0, Math.floor((x - PAD_LEFT) / barWidth))));
            } else {
              let best = 0;
              for (let i = 1; i < n; i++) if (Math.abs(xAt(i) - x) < Math.abs(xAt(best) - x)) best = i;
              setHover(best);
            }
          }}
        >
          {yTickValues.map((v) => (
            <g key={v}>
              <line
                x1={PAD_LEFT}
                x2={WIDTH - right}
                y1={yAt(v)}
                y2={yAt(v)}
                stroke="currentColor"
                opacity={v === yMin ? 0.35 : 0.12}
              />
              <text
                x={PAD_LEFT - 6}
                y={yAt(v) + 4}
                textAnchor="end"
                fontSize="11"
                fill="currentColor"
                opacity={0.7}
              >
                {formatNumber(v)}
              </text>
            </g>
          ))}
          {data.x.map(
            (x, i) =>
              (i % labelEvery === 0 || i === n - 1) && (
                <text
                  key={i}
                  x={kind === "bar" ? PAD_LEFT + ((WIDTH - PAD_LEFT - right) / n) * (i + 0.5) : xAt(i)}
                  y={height - PAD_BOTTOM + 16}
                  textAnchor="middle"
                  fontSize="11"
                  fill="currentColor"
                  opacity={0.7}
                >
                  {x}
                </text>
              ),
          )}
          {kind === "bar"
            ? series.map((s, k) => {
                const barWidth = (WIDTH - PAD_LEFT - right) / n;
                const gap = 2;
                const w = Math.max(2, (barWidth - 8) / series.length - gap);
                return s.values.map((v, i) =>
                  v === null || v === undefined ? null : (
                    <rect
                      key={`${k}-${i}`}
                      x={PAD_LEFT + barWidth * i + 4 + k * (w + gap)}
                      y={yAt(v)}
                      width={w}
                      height={Math.max(0, yAt(yMin) - yAt(v))}
                      rx={2}
                      fill={SERIES_COLORS[k]}
                      opacity={hover === null || hover === i ? 1 : 0.45}
                    />
                  ),
                );
              })
            : series.map((s, k) => {
                const points = s.values.map((v, i) =>
                  v === null || v === undefined ? null : `${xAt(i)},${yAt(v)}`,
                );
                const segments: string[] = [];
                let current: string[] = [];
                for (const p of points) {
                  if (p) current.push(p);
                  else {
                    if (current.length) segments.push(current.join(" "));
                    current = [];
                  }
                }
                if (current.length) segments.push(current.join(" "));
                const lastIdx = lastOf(s);
                return (
                  <g key={k}>
                    {segments.map((d, j) => (
                      <polyline
                        key={j}
                        points={d}
                        fill="none"
                        stroke={SERIES_COLORS[k]}
                        strokeWidth={2}
                        strokeLinejoin="round"
                        strokeLinecap="round"
                      />
                    ))}
                    {s.values.map((v, i) =>
                      v === null || v === undefined ? null : (
                        <circle
                          key={i}
                          cx={xAt(i)}
                          cy={yAt(v)}
                          r={hover === i ? 5 : 3.5}
                          fill={SERIES_COLORS[k]}
                          stroke="var(--sheet)"
                          strokeWidth={1.5}
                        />
                      ),
                    )}
                  </g>
                );
              })}
          {kind === "line" &&
            series.length > 1 &&
            series.map((s, k) => {
              const lastIndex = lastOf(s);
              return lastIndex === undefined ? null : (
                <text
                  key={`l${k}`}
                  x={xAt(lastIndex) + 8}
                  y={(labelY[k] ?? yAt(s.values[lastIndex] as number)) + 4}
                  fontSize="11"
                  fill={SERIES_COLORS[k]}
                  paintOrder="stroke"
                  stroke="var(--sheet)"
                  strokeWidth={3}
                >
                  {s.name}
                </text>
              );
            })}
          {hover !== null &&
            (() => {
              const x =
                kind === "bar" ? PAD_LEFT + ((WIDTH - PAD_LEFT - right) / n) * (hover + 0.5) : xAt(hover);
              const lines = [
                data.x[hover],
                ...series.map(
                  (s) => `${series.length > 1 ? s.name + " " : ""}${formatNumber(s.values[hover], unit)}`,
                ),
              ];
              const barWidth = Math.max(...lines.map((l) => l.length)) * 6.6 + 16;
              const boxX = Math.min(Math.max(PAD_LEFT, x - barWidth / 2), WIDTH - right - barWidth);
              const boxY = PAD_TOP;
              return (
                <g pointerEvents="none">
                  <line
                    x1={x}
                    x2={x}
                    y1={PAD_TOP}
                    y2={height - PAD_BOTTOM}
                    stroke="currentColor"
                    opacity={0.25}
                    strokeDasharray="3 3"
                  />
                  <rect
                    x={boxX}
                    y={boxY}
                    width={barWidth}
                    height={14 * lines.length + 8}
                    rx={4}
                    fill="var(--sheet)"
                    stroke="var(--rule-strong)"
                  />
                  {lines.map((l, i) => (
                    <text
                      key={i}
                      x={boxX + 8}
                      y={boxY + 14 * (i + 1)}
                      fontSize="11"
                      fill="currentColor"
                      fontWeight={i === 0 ? 600 : 400}
                    >
                      {l}
                    </text>
                  ))}
                </g>
              );
            })()}
        </svg>
      )}
      <div className="chart-foot">
        <span className="muted small">{unit ? `단위 ${unit}` : ""}</span>
        <button className="ghost small" onClick={() => setAsTable((v) => !v)}>
          {asTable ? "그래프로 보기" : "표로 보기"}
        </button>
      </div>
    </div>
  );
}
