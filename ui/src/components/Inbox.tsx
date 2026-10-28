import { useCallback, useEffect, useMemo, useState } from "react";
import { apiAt } from "../api";
import type { Approval } from "../types";
import ApprovalSlip from "./ApprovalCard";
import { toolLabel } from "./EventList";
import type { TrayItem } from "./Rail";

const STATUS_KO: Record<string, string> = {
  pending: "대기",
  approved: "승인",
  denied: "반려",
  expired: "기한 만료",
};
interface Row {
  base: string;
  agent: string;
  a: Approval;
}

/** "결재함" 탭: 모든 비서의 대기 전표 + 처리 내역. 비서·상태로 거른다. */
export default function Inbox({
  bases,
  nameOf,
  tray,
  onDecided,
  canApprove,
}: {
  bases: string[];
  nameOf: (b: string) => string;
  tray: TrayItem[];
  onDecided: () => void;
  canApprove: boolean;
}) {
  const [all, setAll] = useState<Row[]>([]);
  const [agent, setAgent] = useState("");
  const [status, setStatus] = useState("");
  const [kept, setKept] = useState<Record<string, TrayItem & { d: "approve" | "deny" }>>({});
  const load = useCallback(async () => {
    const got = await Promise.all(
      bases.map(async (b) => {
        try {
          return (await apiAt(b).approvals("all")).map((a) => ({ base: b, agent: nameOf(b), a }));
        } catch {
          return [] as Row[];
        }
      }),
    );
    setAll(got.flat().sort((x, y) => y.a.created_at.localeCompare(x.a.created_at)));
  }, [bases, nameOf]);
  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);
  const decide = (item: TrayItem) => async (id: string, d: "approve" | "deny") => {
    await apiAt(item.base).decide(id, d);
    setKept((k) => ({ ...k, [id]: { ...item, d } }));
    setTimeout(
      () =>
        setKept((k) => {
          const n = { ...k };
          delete n[id];
          return n;
        }),
      6000,
    );
    onDecided();
    load();
  };
  const shown: (TrayItem & { d?: "approve" | "deny" })[] = [
    ...tray.filter((t) => !kept[t.a.id]),
    ...Object.values(kept),
  ];
  const history = useMemo(
    () =>
      all
        .filter(
          (r) =>
            r.a.status !== "pending" && (!agent || r.base === agent) && (!status || r.a.status === status),
        )
        .slice(0, 100),
    [all, agent, status],
  );
  return (
    <main className="desk page">
      <div className="deskhead">
        <h1>결재함</h1>
        <p>어느 비서가 올린 결재든 여기서 처리합니다. 승인하면 그 비서가 바로 실행합니다.</p>
      </div>
      <section className="block">
        <h2>
          대기 중 <span className="muted">{tray.length ? `${tray.length}건` : "없음"}</span>
        </h2>
        {shown.length === 0 ? (
          <p className="empty">기다리는 결재가 없습니다.</p>
        ) : (
          <div className="slips">
            {shown.map((it) => (
              <ApprovalSlip
                key={it.a.id}
                a={{
                  approval_id: it.a.id,
                  tool: it.a.tool,
                  arguments: it.a.args,
                  tier: it.a.tier,
                  run_id: it.a.run_id,
                }}
                onDecide={decide(it)}
                decided={it.d ?? null}
                canApprove={canApprove}
                from={it.agent}
              />
            ))}
          </div>
        )}
      </section>
      <section className="block">
        <h2>
          처리 내역 <span className="muted">{history.length}건</span>
        </h2>
        <div className="filters">
          <select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="비서">
            <option value="">모든 비서</option>
            {bases.map((b) => (
              <option key={b || "self"} value={b}>
                {nameOf(b)}
              </option>
            ))}
          </select>
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="상태">
            <option value="">모든 상태</option>
            {["approved", "denied", "expired"].map((s) => (
              <option key={s} value={s}>
                {STATUS_KO[s]}
              </option>
            ))}
          </select>
        </div>
        {history.length === 0 ? (
          <p className="empty">처리한 결재가 없습니다.</p>
        ) : (
          <div className="tablewrap">
            <table className="data wide">
              <thead>
                <tr>
                  <th>시각</th>
                  <th>비서</th>
                  <th>요청</th>
                  <th>내용</th>
                  <th>상태</th>
                  <th>결재자</th>
                </tr>
              </thead>
              <tbody>
                {history.map((r) => (
                  <tr key={r.a.id}>
                    <td>{(r.a.decided_at ?? r.a.created_at).slice(5, 16).replace("T", " ")}</td>
                    <td>{r.agent}</td>
                    <td>{toolLabel(r.a.tool)}</td>
                    <td className="wrap">
                      {Object.entries(r.a.args)
                        .filter(([, v]) => v !== null && v !== "")
                        .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
                        .join(" · ")
                        .slice(0, 120)}
                    </td>
                    <td>
                      <span className={`st-${r.a.status}`}>{STATUS_KO[r.a.status] ?? r.a.status}</span>
                    </td>
                    <td>{r.a.decided_by ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
