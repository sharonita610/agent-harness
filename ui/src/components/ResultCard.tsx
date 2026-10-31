import { fileUrl } from "../api";
import Rich from "./Rich";

type Row = Record<string, unknown>;
const isPrimitive = (v: unknown) => v === null || ["string", "number", "boolean"].includes(typeof v);
const isFlatRow = (v: unknown): v is Row =>
  !!v && typeof v === "object" && !Array.isArray(v) && Object.values(v as Row).every(isPrimitive);
/** 식별자 열(주문 번호, id, 로트 …)은 숫자여도 수량이 아니다: 천 단위 쉼표를 붙이지 않고 왼쪽 정렬한다. */
const ID_COLUMN = /^(id|.*_id|.*id|주문|번호|주문\s*번호|이벤트|로트|티켓|실행|문서)$/i;
export const isIdColumn = (name: string) => ID_COLUMN.test(name.trim());
const formatValue = (v: unknown): string => {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("ko-KR") : String(+v.toFixed(4));
  if (typeof v === "boolean") return v ? "예" : "아니요";
  if (typeof v === "string") return v.length > 90 ? v.slice(0, 90) + "…" : v;
  return JSON.stringify(v);
};
const KEY_KO: Record<string, string> = {
  row_count: "행 수",
  truncated: "잘림",
  sql_executed: "실행한 SQL",
  measurements: "측정값",
  measurements_readable: "측정값",
  points: "점 수",
  elapsed_ms: "소요(ms)",
  analysis: "해석",
  warnings: "경고",
  errors: "오류",
  run_id: "실행",
  answer: "답",
  agent: "비서",
  delegated: "위임",
  parallel: "동시 실행 수",
  elapsed_s: "소요(s)",
  state: "상태",
  task_id: "태스크",
  count: "건수",
  total_matching: "전체 건수",
  change_pct: "변화(%)",
  verdict: "판정",
  station: "스테이션",
  from: "시작",
  to: "끝",
  note: "안내",
  next: "다음",
  source: "출처",
  score: "점수",
  mode: "방식",
  ng_rate_pct: "NG율(%)",
  inspected: "검사 수",
  ng: "NG 수",
  multiplier: "배수",
  baseline_rate_pct: "기준(%)",
  rate_pct: "NG율(%)",
  top_defect: "대표 불량",
  shift: "교대",
  hours: "시간",
  start: "시작",
  end: "끝",
  value: "값",
  z: "z",
  diff: "차이",
  param: "파라미터",
  window_mean: "구간 평균",
  baseline_mean: "평소 평균",
  doc: "문서",
  section: "절",
  text: "본문",
  snippet: "발췌",
  query: "검색어",
  hits: "검색 결과",
  agents: "비서",
  id: "id",
  name: "이름",
  description: "설명",
  title: "제목",
  title_path: "절 경로",
  page_start: "시작 쪽",
  page_end: "끝 쪽",
  spikes: "급증 구간",
  events: "NG 기록",
  runs: "실행 기록",
  circuits: "회로",
  by_defect: "불량 유형별",
  param_candidates: "파라미터 후보",
  maintenance_near_window: "구간 근처 정비",
  equipment: "설비",
  window: "구간",
  group_by: "묶음",
  stations: "스테이션 수",
  total_ng: "NG 합계",
  returned: "반환 행",
  total_points: "전체 점 수",
  series: "계열",
  saved: "저장됨",
  deleted: "삭제됨",
  components: "소자 수",
  analyses: "해석",
  summary: "요약",
  estimated_points: "예상 점 수",
  ok: "통과",
  before: "이전",
  after: "이후",
  action: "조치",
  kind: "종류",
  component: "소자",
  measurement: "측정",
  analysis_kind: "해석 종류",
  failed: "실패",
  netlist: "넷리스트",
  path: "경로",
  line: "라인",
  order_id: "주문",
  customer_id: "거래처",
  status: "상태",
};
const labelOf = (k: string) => KEY_KO[k] ?? k;
const HIDDEN_KEYS = new Set([
  "note",
  "next",
  "sql_executed",
  "saved_vectors",
  "vectors",
  "ngspice",
  "svg_path",
  "truncated",
  "columns",
  "rows",
  "url",
  "measurements",
  "task_id",
  "history",
  "errors",
]);

export function Table({ rows, max = 12 }: { rows: Row[]; max?: number }) {
  if (!rows.length) return <p className="muted small">결과 없음</p>;
  const cols = Array.from(
    rows.reduce((s, r) => {
      Object.keys(r).forEach((k) => s.add(k));
      return s;
    }, new Set<string>()),
  );
  const shown = rows.slice(0, max);
  const idCols = new Set(cols.filter(isIdColumn));
  const numClass = (c: string, v: unknown) => (!idCols.has(c) && typeof v === "number" ? "num" : "");
  return (
    <div className="tablewrap">
      <table className="data">
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} className={numClass(c, shown[0]?.[c])}>
                {labelOf(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c} className={numClass(c, r[c])}>
                  {idCols.has(c) ? String(r[c] ?? "") : formatValue(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > max && <p className="muted small">{rows.length - max}행 더 있음</p>}
    </div>
  );
}

/** 검색 결과: 출처 한 줄 + 발췌. 긴 본문은 접지 않고 160자에서 자른다. */
function Sources({ hits }: { hits: Row[] }) {
  return (
    <ul className="sources">
      {hits.slice(0, 6).map((h, i) => {
        const t = String(h.snippet ?? h.text ?? "").replace(/\s+/g, " ");
        return (
          <li key={i}>
            <b>{String(h.source)}</b>
            {t ? (
              <span>
                {" "}
                {t.slice(0, 160)}
                {t.length > 160 ? "…" : ""}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function KeyValues({ obj, keys }: { obj: Row; keys?: string[] }) {
  const ks = (keys ?? Object.keys(obj)).filter(
    (k) => !HIDDEN_KEYS.has(k) && isPrimitive(obj[k]) && obj[k] !== "" && obj[k] !== null,
  );
  if (!ks.length) return null;
  return (
    <dl className="kv">
      {ks.map((k) => (
        <div key={k}>
          <dt>{labelOf(k)}</dt>
          <dd>{formatValue(obj[k])}</dd>
        </div>
      ))}
    </dl>
  );
}

/** 도구 결과 원본 → 사람이 읽는 카드. 모양으로 판단한다: 표 / 측정값 / 출처 목록 / 위임 답 / 그림 / 키·값 / JSON. */
export default function ResultCard({ result, tool }: { result: unknown; tool?: string }) {
  if (result === undefined || result === null) return null;
  if (typeof result === "string") return <p className="card-text">{result}</p>;
  if (Array.isArray(result)) {
    if (result.length && result.every((h) => h && typeof h === "object" && "source" in (h as Row)))
      return <Sources hits={result as Row[]} />;
    if (result.every(isFlatRow)) return <Table rows={result as Row[]} />;
    return (
      <details className="raw">
        <summary>원문 보기</summary>
        <pre>{JSON.stringify(result, null, 1).slice(0, 4000)}</pre>
      </details>
    );
  }
  const r = result as Row;
  const parts: React.ReactNode[] = [];
  // 동시 위임(delegate_many): results 의 항목마다 인용 카드 하나. 결재 대기·실패 항목은 상태를 같이 적는다
  const many = Array.isArray(r.results) ? (r.results as Row[]) : null;
  if (
    many &&
    (tool === "delegate_many" ||
      (many.length > 0 && many.every((x) => x && typeof x === "object" && "agent" in x && "state" in x)))
  ) {
    return (
      <div className="result">
        {many.map((x, i) => (
          <blockquote key={i} className="quote">
            <span className="from">
              {formatValue(x.agent)} 비서의 답
              {x.state && x.state !== "completed" ? ` · ${formatValue(x.state)}` : ""}
            </span>
            <Rich text={String(x.answer || x.waiting_for || x.error || x.note || "")} />
          </blockquote>
        ))}
        {typeof r.parallel === "number" && typeof r.elapsed_s === "number" ? (
          <p className="card-text">{`비서 ${r.parallel}곳에 동시 위임 · ${r.elapsed_s}s`}</p>
        ) : null}
      </div>
    );
  }
  if (tool === "delegate" || (typeof r.answer === "string" && r.agent)) {
    parts.push(
      <blockquote key="q" className="quote">
        <span className="from">
          {formatValue(r.agent)} 비서의 답
          {r.state && r.state !== "completed" ? ` · ${formatValue(r.state)}` : ""}
        </span>
        <Rich text={String(r.answer || r.waiting_for || r.note || "")} />
      </blockquote>,
    );
    return <div className="result">{parts}</div>;
  }
  if (typeof r.url === "string" && /\.(svg|png|jpe?g|webp)(\?.*)?$/i.test(r.url)) {
    parts.push(
      <a key="img" href={fileUrl(r.url)} target="_blank" rel="noreferrer">
        <img className="plot" src={fileUrl(r.url)} alt="그래프" />
      </a>,
    );
  }
  const measured = r.measurements_readable ?? r.measurements;
  if (measured && typeof measured === "object" && !Array.isArray(measured)) {
    const m = measured as Row;
    parts.push(
      <div key="m" className="measures">
        {Object.entries(m).map(([k, v]) => (
          <div key={k}>
            <span className="k">{k}</span>
            <span className="v">{v === null ? "값 없음" : formatValue(v)}</span>
          </div>
        ))}
      </div>,
    );
  }
  if (Array.isArray(r.columns) && Array.isArray(r.rows)) {
    // rows 가 [[...]] 꼴(파형 등)이면 columns 와 짝지어 행 객체로
    const cols = r.columns as string[];
    const rowsObj = (r.rows as unknown[]).map((row) =>
      Array.isArray(row) ? Object.fromEntries(cols.map((c, i) => [c, row[i]])) : (row as Row),
    );
    parts.push(<Table key="t" rows={rowsObj} />);
  } else {
    for (const [k, v] of Object.entries(r)) {
      if (Array.isArray(v) && v.length && v.every(isFlatRow) && !HIDDEN_KEYS.has(k) && k !== "warnings") {
        const rows = v as Row[];
        parts.push(
          <div key={k}>
            <div className="subhead">{labelOf(k)}</div>
            {rows.every((h) => "source" in h) ? <Sources hits={rows} /> : <Table rows={rows} />}
          </div>,
        );
        break;
      }
    }
    for (const [k, v] of Object.entries(r)) {
      if (
        Array.isArray(v) &&
        v.length &&
        v.every((x) => typeof x === "string") &&
        !HIDDEN_KEYS.has(k) &&
        k !== "warnings"
      ) {
        parts.push(
          <div key={k + "-l"}>
            <div className="subhead">{labelOf(k)}</div>
            <ul className="plain">
              {(v as string[]).slice(0, 8).map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          </div>,
        );
        break;
      }
    }
  }
  const visibleKeys = Object.keys(r).filter(
    (k) => !HIDDEN_KEYS.has(k) && isPrimitive(r[k]) && r[k] !== "" && r[k] !== null,
  );
  if (visibleKeys.length) parts.push(<KeyValues key="kv" obj={r} />);
  if (Array.isArray(r.warnings) && r.warnings.length)
    parts.push(
      <p key="w" className="warn small">
        {(r.warnings as string[]).slice(0, 2).join(" · ")}
      </p>,
    );
  // note 는 모델에게 주는 안내문이라 카드에는 싣지 않는다(확인 과정의 원문에는 남는다)
  if (parts.length === 0)
    return (
      <details className="raw">
        <summary>원문 보기</summary>
        <pre>{JSON.stringify(r, null, 1).slice(0, 4000)}</pre>
      </details>
    );
  return <div className="result">{parts}</div>;
}
