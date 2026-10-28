import { useCallback, useEffect, useState } from "react";
import { apiAt, fileUrl } from "../api";
import type { HeatData, Panel, SeriesData, Stat } from "../types";
import Chart from "./Chart";
import { Table } from "./ResultCard";

const fmtv = (v: number | string | null | undefined, unit = "") =>
  v === null || v === undefined
    ? "값 없음"
    : typeof v === "number"
      ? `${Number.isInteger(v) ? v.toLocaleString("ko-KR") : v.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}${unit ? " " + unit : ""}`
      : String(v);

export function Stats({ data }: { data: Stat[] }) {
  return (
    <div className="stats">
      {data.map((s) => (
        <div key={s.label} className={`stat ${s.tone ?? ""}`}>
          <span className="l">{s.label}</span>
          <span className="v">
            {fmtv(s.value)}
            {s.unit && typeof s.value === "number" ? <small> {s.unit}</small> : null}
          </span>
        </div>
      ))}
    </div>
  );
}

/** 라인 × 공정 같은 격자. 값이 클수록 청록이 진해진다(한 색조, 밝기만). 숫자는 항상 글로도 적는다. */
export function Heat({ data }: { data: HeatData }) {
  const all = data.values.flat().filter((v): v is number => typeof v === "number");
  const max = Math.max(...all, 0),
    min = Math.min(...all, max);
  const span = Math.max(max - min, 1e-9);
  return (
    <div className="tablewrap">
      <table className="data heat">
        <thead>
          <tr>
            <th></th>
            {data.cols.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r, i) => (
            <tr key={r}>
              <th>{r}</th>
              {data.cols.map((c, j) => {
                const v = data.values[i]?.[j];
                const a = v === null || v === undefined ? 0 : 0.08 + 0.72 * ((v - min) / span); // 최소~최대 사이로 펼쳐 작은 차이도 보이게
                return (
                  <td
                    key={c}
                    className="num"
                    style={{
                      background: `rgba(31,78,121,${a.toFixed(3)})`,
                      color: a > 0.5 ? "#fff" : undefined,
                    }}
                    title={`${r} ${c}: ${fmtv(v, data.unit)}`}
                  >
                    {v === null || v === undefined ? "" : fmtv(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PanelBody({ p }: { p: Panel }) {
  switch (p.kind) {
    case "stats":
      return <Stats data={p.data as Stat[]} />;
    case "table": {
      const d = p.data as { rows: Record<string, unknown>[] };
      return d.rows.length ? <Table rows={d.rows} max={20} /> : <p className="muted small">해당 없음</p>;
    }
    case "line":
    case "bar":
      return <Chart kind={p.kind} data={p.data as SeriesData} />;
    case "heat":
      return <Heat data={p.data as HeatData} />;
    case "image": {
      const d = p.data as { url: string; alt?: string };
      return (
        <a href={fileUrl(d.url)} target="_blank" rel="noreferrer">
          <img
            className="plot"
            src={fileUrl(d.url) + (d.url.includes("?") ? "&" : "?") + "t=" + Math.floor(Date.now() / 60000)}
            alt={d.alt ?? p.title}
          />
        </a>
      );
    }
    default:
      return null;
  }
}

/** "데이터" 탭: 비서를 거치지 않고 원 데이터를 본다. 패널은 팩이 정하고, 화면은 모양대로 그린다. 30초마다 새로 고친다. */
export default function DataView({ base, agentName }: { base: string; agentName: string }) {
  const [panels, setPanels] = useState<Panel[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [at, setAt] = useState<Date | null>(null);
  const load = useCallback(async () => {
    try {
      setPanels(await apiAt(base).dashboard());
      setErr(null);
      setAt(new Date());
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [base]);
  useEffect(() => {
    setPanels(null);
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);
  return (
    <main className="desk page">
      <div className="deskhead">
        <h1>{agentName} · 데이터</h1>
        <p>
          비서가 보는 것과 같은 데이터를 비서 없이 봅니다.
          {at ? ` ${at.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })} 기준` : ""}{" "}
          <button className="ghost small" onClick={load}>
            새로 고침
          </button>
        </p>
      </div>
      {err && <p className="error">{err}</p>}
      {panels === null && !err && <p className="muted">불러오는 중</p>}
      {panels && panels.length === 0 && <p className="empty">이 비서에는 데이터 화면이 없습니다.</p>}
      {panels && panels.length > 0 && (
        <div className="panels">
          {panels.map((p) => (
            <section key={p.id} className={`panel ${p.span === 2 ? "span2" : ""}`}>
              <h2>{p.title}</h2>
              <PanelBody p={p} />
              {p.note && <p className="muted small note">{p.note}</p>}
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
