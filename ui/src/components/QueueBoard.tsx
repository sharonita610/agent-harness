import type { Progress, Run } from "../types";
import { toolLabel } from "./EventList";

const STATUS_KO: Record<string, string> = {
  queued: "대기",
  running: "실행 중",
  done: "완료",
  failed: "실패",
  cancelled: "취소됨",
};
const toMillis = (s: string | null | undefined) => (s ? new Date(s).getTime() : NaN); // 저장소 시각은 로컬 시간(Z 없음). Z 를 붙이면 9시간 미래가 된다
const formatDuration = (x: number) =>
  x >= 60000 ? `${(x / 60000).toFixed(1)}분` : `${Math.max(0, x / 1000).toFixed(x >= 10000 ? 0 : 1)}초`;

/** 큐 보드: 최근 작업을 시간선 위에 막대로. 대기는 빈 막대(기다린 시간), 실행 중은 채워지는 막대 + 진행률, 끝난 것은 소요 시간만큼. 누르면 연다. */
export default function QueueBoard({
  jobs,
  progress,
  now = Date.now(),
  onOpen,
}: {
  jobs: Run[];
  progress: Record<string, Progress>;
  now?: number;
  onOpen: (id: string) => void;
}) {
  const rows = jobs.slice(0, 12);
  if (!rows.length) return null;
  // 시간선은 최근 10분 + 진행 중인 작업의 시작 시각까지. 그보다 오래된 작업은 왼쪽 끝에 붙는다
  const activeStarts = rows
    .filter((j) => ["queued", "running"].includes(j.status))
    .map((j) => toMillis(j.started_at) || toMillis(j.created_at))
    .filter((x) => !isNaN(x));
  const t0 = Math.min(now - 10 * 60000, ...activeStarts);
  const span = Math.max(now - t0, 60000);
  const percentAt = (t: number) => Math.min(100, Math.max(0, ((t - t0) / span) * 100));
  const queueOrder = rows.filter((j) => j.status === "queued").map((j) => j.id);
  return (
    <div className="qboard" role="list" aria-label="작업 큐 시간선">
      {rows.map((j) => {
        const s = toMillis(j.started_at) || toMillis(j.created_at);
        const e = toMillis(j.finished_at) || now;
        const left = percentAt(s);
        const width = Math.max(0.6, percentAt(e) - left);
        const p = progress[j.id];
        const pct = p && p.total > 0 ? Math.min(100, (100 * p.done) / p.total) : null;
        const waited = j.status === "queued" ? now - toMillis(j.created_at) : null;
        const title =
          j.status === "queued"
            ? `${queueOrder.indexOf(j.id) + 1}번째로 대기 · ${formatDuration(waited ?? 0)} 기다림`
            : j.status === "running"
              ? p
                ? `${toolLabel(p.name)} ${p.done}/${p.total}${p.label ? " · " + p.label : ""}`
                : `실행 중 · ${formatDuration(now - s)}`
              : `${STATUS_KO[j.status] ?? j.status} · ${formatDuration(e - s)}`;
        return (
          <button
            key={j.id}
            role="listitem"
            className={`qrow st-${j.status}`}
            onClick={() => onOpen(j.id)}
            title={`${j.question}\n${title}`}
          >
            <span className="qq">{j.question}</span>
            <span className="qtrack">
              <span className={`qbar ${j.status}`} style={{ left: `${left}%`, width: `${width}%` }}>
                {pct !== null && <span className="qfill" style={{ width: `${pct}%` }} />}
              </span>
            </span>
            <span className="qside">
              {j.status === "queued"
                ? `${queueOrder.indexOf(j.id) + 1}번째 대기`
                : j.status === "running" && p
                  ? `${p.done}/${p.total}${p.label ? " · " + p.label : ""}`
                  : title}
            </span>
          </button>
        );
      })}
    </div>
  );
}
