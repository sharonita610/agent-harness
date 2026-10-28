import { useEffect, useRef, useState } from "react";
import { apiAt } from "./api";
import type { Progress, RunEvent } from "./types";

/** 실행 중인 run 들의 최신 progress 이벤트를 2초마다 모은다. 끝난 run 은 더 묻지 않는다. */
export function useProgress(base: string, runIds: string[]): Record<string, Progress> {
  const [map, setMap] = useState<Record<string, Progress>>({});
  const seqs = useRef<Record<string, number>>({});
  const key = runIds.join(",");
  useEffect(() => {
    if (!runIds.length) { setMap({}); return; }
    let alive = true;
    const tick = async () => {
      const api = apiAt(base);
      await Promise.all(runIds.map(async (id) => {
        try {
          const evs = (await api.runEvents(id, seqs.current[id] ?? 0)) as RunEvent[];
          if (!evs.length) return;
          seqs.current[id] = evs[evs.length - 1].seq;
          const last = [...evs].reverse().find((e) => e.kind === "progress");
          if (last && alive) setMap((m) => ({ ...m, [id]: { name: String(last.data.name), done: Number(last.data.done), total: Number(last.data.total), label: String(last.data.label ?? ""), at: last.ts } }));
        } catch { /* 서버 다운 */ }
      }));
    };
    tick(); const t = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(t); };
  }, [base, key]);  // eslint-disable-line react-hooks/exhaustive-deps
  return map;
}
