"""더미 데이터 개요: README 용 마크다운 표 + docs/img/dataset-overview.svg (의존성 없음).
사용: uv run python scripts/data_report.py  → README 의 DATASET 마커 사이를 갱신하고 SVG 를 쓴다.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "enterprise.db"
SVG = ROOT / "docs" / "img" / "dataset-overview.svg"
README = ROOT / "README.md"

TABLES = [
    ("customers", "거래처", "등급 A/B/C, 지역, 담당자, 연락처(마스킹 대상), 결제 조건"),
    ("products", "상품", "카테고리 5종, 단가"),
    ("warehouses", "창고", "수도권 1창고(경기), 남부 2창고(부산)"),
    ("inventory", "재고", "창고 × 상품, 현재고, 안전재고"),
    ("orders", "주문", "상태 6종, 금액, 지연 사유, 할인율"),
    ("order_items", "주문 상품", "주문 × 상품, 수량, 단가"),
    ("shipments", "배송", "운송사 3곳, 출고 예정일 / 실제 출고일 / 배송 완료일"),
    ("order_notes", "주문 메모", "작성자, 메모"),
    ("order_status_history", "상태 이력", "이전 → 이후 상태, 사유, 변경자"),
]


def q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def collect():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    counts = {t: q(conn, f"SELECT COUNT(*) FROM {t}")[0][0] for t, _, _ in TABLES}
    status = q(conn, "SELECT status, COUNT(*), SUM(total_amount) FROM orders GROUP BY status ORDER BY 2 DESC")
    reasons = q(
        conn, "SELECT delay_reason, COUNT(*) FROM orders WHERE status='지연' GROUP BY delay_reason ORDER BY 2 DESC"
    )
    grades = q(conn, "SELECT grade, COUNT(*) FROM customers GROUP BY grade ORDER BY grade")
    low = q(conn, "SELECT SUM(on_hand < safety_stock), SUM(on_hand >= safety_stock) FROM inventory")[0]
    cats = q(conn, "SELECT category, COUNT(*), MIN(unit_price), MAX(unit_price) FROM products GROUP BY category")
    span = q(conn, "SELECT MIN(order_date), MAX(order_date) FROM orders")[0]
    docs = sorted(p.stem for p in (ROOT / "harness/packs/enterprise/docs").glob("*.md"))
    conn.close()
    return {
        "counts": counts,
        "status": status,
        "reasons": reasons,
        "grades": grades,
        "low": low,
        "cats": cats,
        "span": span,
        "docs": docs,
    }


def won(n: int) -> str:
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}억 원"
    if n >= 10_000:
        return f"{n // 10_000:,}만 원"
    return f"{n:,}원"


def markdown(d) -> str:
    lines = ["| 테이블 | 행 수 | 담고 있는 것 |", "|---|---|---|"]
    for t, ko, desc in TABLES:
        lines.append(f"| {ko} (`{t}`) | {d['counts'][t]:,} | {desc} |")
    lines += [
        "",
        f"주문 기간 {d['span'][0]} ~ {d['span'][1]} (최근 1년). 거래처 등급 "
        + ", ".join(f"{g} {n}곳" for g, n in d["grades"])
        + f". 상품은 5개 카테고리 각 {d['cats'][0][1]}개, 단가 {d['cats'][0][2]:,}~{d['cats'][0][3]:,}원. 안전재고 미달 항목 {d['low'][0]}개 / {d['low'][0] + d['low'][1]}개.",
        "",
        "| 주문 상태 | 건수 | 금액 합계 |",
        "|---|---|---|",
    ]
    lines += [f"| {s} | {n:,} | {won(a)} |" for s, n, a in d["status"]]
    lines += [
        "",
        "지연 사유 ("
        + f"{sum(n for _, n in d['reasons'])}건): "
        + ", ".join(f"{r} {n}" for r, n in d["reasons"])
        + ".",
        "",
        f"규정 문서 {len(d['docs'])}개: "
        + ", ".join(x.split("-", 1)[1].replace("-", " ") for x in d["docs"])
        + ". 이 중 '거래처 등급 기준' 과 '창고 운영 지침' 에 함정 지시문이 들어 있다.",
        "",
        "![데이터셋 개요](docs/img/dataset-overview.svg)",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- SVG
BLUE = "#2a78d6"
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e6e4dc", "#fcfcfb"
FONT = "-apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Noto Sans KR', Helvetica, Arial, sans-serif"


def bars(x, y, w, title, rows, unit=""):
    """가로 막대 패널. rows = [(label, value)], 단일 측정값 → 단일 색."""
    out = [f'<text x="{x}" y="{y}" font-size="13" font-weight="600" fill="{INK}">{title}</text>']
    label_w = 86
    maxv = max(v for _, v in rows) or 1
    bar_x = x + label_w
    bar_w = w - label_w - 64
    bh, gap = 16, 10
    yy = y + 14
    for label, v in rows:
        bw = max(2, round(bar_w * v / maxv))
        r = 4 if bw > 8 else 0
        out.append(
            f'<text x="{bar_x - 8}" y="{yy + bh - 4}" font-size="12" text-anchor="end" fill="{INK2}">{label}</text>'
        )
        out.append(
            f'<path d="M{bar_x} {yy} h{bw - r} a{r} {r} 0 0 1 {r} {r} v{bh - 2 * r} a{r} {r} 0 0 1 -{r} {r} h-{bw - r} z" fill="{BLUE}"/>'
        )
        out.append(f'<text x="{bar_x + bw + 6}" y="{yy + bh - 4}" font-size="12" fill="{INK}">{v:,}{unit}</text>')
        yy += bh + gap
    return "\n".join(out), yy


def svg(d) -> str:
    W = 760
    parts = [
        f'<rect width="{W}" height="H" fill="{SURF}" rx="12"/>',
        f'<text x="20" y="30" font-size="16" font-weight="600" fill="{INK}">한빛상사 더미 데이터 개요</text>',
        f'<text x="20" y="50" font-size="12" fill="{INK2}">주문 {d["counts"]["orders"]:,}건 · 거래처 {d["counts"]["customers"]}곳 · 상품 {d["counts"]["products"]}개 · 규정 문서 {len(d["docs"])}개 · 시드 고정(재생성해도 동일)</text>',
    ]
    col_w = (W - 60) // 2
    p1, y1 = bars(20, 86, col_w, "주문 상태별 건수", [(s, n) for s, n, _ in d["status"]], "건")
    p2, y2 = bars(40 + col_w, 86, col_w, "지연 사유 (지연 주문 기준)", [(r, n) for r, n in d["reasons"]], "건")
    top = max(y1, y2) + 16
    p3, y3 = bars(20, top + 14, col_w, "거래처 등급", [(f"등급 {g}", n) for g, n in d["grades"]], "곳")
    p4, y4 = bars(
        40 + col_w, top + 14, col_w, "재고 항목 (안전재고 기준)", [("미달", d["low"][0]), ("정상", d["low"][1])], "개"
    )
    bottom = max(y3, y4) + 10
    body = "\n".join([parts[0].replace("H", str(bottom)), *parts[1:], p1, p2, p3, p4])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{bottom}" viewBox="0 0 {W} {bottom}" role="img" '
        f'aria-label="한빛상사 더미 데이터 개요" font-family="{FONT}">\n{body}\n</svg>\n'
    )


if __name__ == "__main__":
    d = collect()
    SVG.parent.mkdir(parents=True, exist_ok=True)
    SVG.write_text(svg(d), encoding="utf-8")
    md = markdown(d)
    text = README.read_text(encoding="utf-8")
    block = f"<!-- DATASET_START -->\n{md}\n<!-- DATASET_END -->"
    if "<!-- DATASET_START -->" in text:
        text = re.sub(r"<!-- DATASET_START -->.*?<!-- DATASET_END -->", lambda _: block, text, flags=re.S)
    else:
        text = text.replace(
            "## 10분 안에 돌려보기",
            f'## 데이터셋 (가짜 회사 "한빛상사")\n`uv run harness seed` 로 생성. 시드가 고정돼 있어 몇 번을 다시 만들어도 같은 데이터다. 아래 표와 그림은 `scripts/data_report.py` 가 DB 에서 뽑아 갱신한다.\n\n{block}\n\n## 10분 안에 돌려보기',
        )
    README.write_text(text, encoding="utf-8")
    print(f"SVG: {SVG.relative_to(ROOT)} · README 데이터셋 절 갱신")
