"""회사 데이터 비서의 "데이터" 탭. 사용자 범위(scope)가 있으면 같은 임시 뷰로 걸러서 비서가 보는 것과 같은 범위만 보인다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...tools.sql import rows
from .scope import Scope
from .seed import DEFAULT_DB, REFERENCE_DATE
from .tools import FILES_DIR, connect_ro, make_graph


def dashboard(
    scope: Scope | None = None, db_path: Path = DEFAULT_DB, files_dir: Path = FILES_DIR
) -> list[dict[str, Any]]:
    ro = connect_ro(db_path, scope)
    query = lambda sql, params=(): rows(ro.execute(sql, params))["rows"]
    total = query("SELECT COUNT(*) AS n FROM orders")[0]["n"]
    delayed = query("SELECT COUNT(*) AS n FROM orders WHERE status='지연'")[0]["n"]
    short = query("SELECT COUNT(*) AS n FROM inventory WHERE on_hand < safety_stock")[0]["n"]
    open_notes = query(
        "SELECT COUNT(*) AS n FROM order_notes WHERE created_at >= date(?, '-7 days')", (REFERENCE_DATE.isoformat(),)
    )[0]["n"]
    status = query("SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY n DESC")
    monthly = query(
        "SELECT substr(order_date,1,7) AS m, COUNT(*) AS n, SUM(status='지연') AS d FROM orders WHERE order_date >= date(?, '-6 months') GROUP BY m ORDER BY m",
        (REFERENCE_DATE.isoformat(),),
    )
    reasons = query(
        "SELECT delay_reason AS 사유, COUNT(*) AS 건수 FROM orders WHERE status='지연' GROUP BY 1 ORDER BY 2 DESC"
    )
    shortage = query(
        "SELECT w.name AS 창고, p.name AS 품목, i.on_hand AS 재고, i.safety_stock AS 안전재고, i.safety_stock - i.on_hand AS 부족 "
        "FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id JOIN products p ON p.id=i.product_id WHERE i.on_hand < i.safety_stock ORDER BY 부족 DESC LIMIT 10"
    )
    recent = query(
        "SELECT o.id AS 주문, c.name AS 거래처, o.order_date AS 주문일, o.delay_reason AS 사유, o.total_amount AS 금액 "
        "FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.status='지연' ORDER BY o.order_date DESC LIMIT 8"
    )
    scope_text = f" · 범위: {scope.describe()}" if scope is not None else ""
    panels = [
        {
            "id": "stats",
            "title": "한눈에" + scope_text,
            "kind": "stats",
            "span": 2,
            "data": [
                {"label": "전체 주문", "value": total, "unit": "건"},
                {"label": "지연 주문", "value": delayed, "unit": "건", "tone": "bad" if delayed else "ok"},
                {"label": "지연율", "value": round(100.0 * delayed / total, 1) if total else 0, "unit": "%"},
                {"label": "안전재고 미달 품목", "value": short, "unit": "개", "tone": "warn" if short else "ok"},
                {"label": "최근 7일 메모", "value": open_notes, "unit": "건"},
            ],
        },
        {
            "id": "status",
            "title": "주문 상태 분포",
            "kind": "bar",
            "data": {
                "x": [r["status"] for r in status],
                "series": [{"name": "주문 수", "values": [r["n"] for r in status]}],
                "unit": "건",
            },
        },
        {
            "id": "monthly",
            "title": "월별 주문과 지연 (최근 6개월)",
            "kind": "line",
            "data": {
                "x": [r["m"] for r in monthly],
                "series": [
                    {"name": "주문 수", "values": [r["n"] for r in monthly]},
                    {"name": "지연 수", "values": [r["d"] for r in monthly]},
                ],
                "unit": "건",
            },
        },
        {
            "id": "reasons",
            "title": "지연 사유별 건수",
            "kind": "table",
            "data": {"columns": ["사유", "건수"], "rows": reasons},
        },
        {
            "id": "shortage",
            "title": "안전재고 미달 품목",
            "kind": "table",
            "data": {"columns": ["창고", "품목", "재고", "안전재고", "부족"], "rows": shortage},
            "note": "재고 부족 사유의 지연은 규정상 회사 책임이다(주문 상태 정의 > 지연 사유 코드).",
        },
        {
            "id": "recent",
            "title": "최근 지연 주문",
            "kind": "table",
            "span": 2,
            "data": {"columns": ["주문", "거래처", "주문일", "사유", "금액"], "rows": recent},
        },
    ]
    # 규정 문서 관계도: 문서 그래프(ADR-023). 범위와 무관하게 문서는 모두 보인다.
    try:
        graph = make_graph()
        files_dir.mkdir(parents=True, exist_ok=True)
        (files_dir / "docgraph.svg").write_text(graph.render_svg(), encoding="utf-8")
        stats = graph.stats()
        panels.append(
            {
                "id": "docgraph",
                "title": f"규정 문서 관계도 (문서 {stats['docs']}개 · 인용 {stats['by_kind'].get('참조', 0) + stats['by_kind'].get('주제', 0)}건 · 같은 데이터 {stats['by_kind'].get('데이터', 0)}쌍)",
                "kind": "image",
                "span": 2,
                "data": {"url": "/files/enterprise/docgraph.svg", "alt": "규정 문서 관계도"},
                "note": "화살표는 문서가 다른 문서의 번호·제목·절을 인용하는 관계, 점선은 같은 데이터 개념(안전재고, 지연 사유 …)을 다루는 관계. 비서의 related_docs·impact_of 가 보는 것과 같은 그래프다.",
            }
        )
        panels.append(
            {
                "id": "docs",
                "title": "규정 문서",
                "kind": "table",
                "span": 2,
                "data": {
                    "columns": ["문서", "제목", "담당", "개정일", "인용함", "인용됨", "데이터 개념"],
                    "rows": graph.table(),
                },
            }
        )
    except Exception:
        pass
    return panels
