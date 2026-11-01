"""사용자별 데이터 범위(row-level): 같은 이름의 임시 뷰로 거래처·주문·배송·메모·이력을 걸러낸다.

SQLite 는 이름이 겹치면 temp 스키마를 먼저 찾으므로, 모델이 쓰는 임의의 SELECT 도 범위 밖 행을 볼 수 없다.
`main.orders` 처럼 스키마를 명시한 우회는 SQL 가드가 막는다(tools.guard_select).

기본 거부(ADR-033): 범위 뷰(SCOPED_TABLES)도 공용 참조 데이터(SHARED_TABLES)도 아닌 테이블은 범위 사용자에게 빈 뷰로 덮는다.
실데이터에 나중에 붙는 테이블(payments 같은)이 "아직 분류 안 됨" 상태로 전부 보이는 일을 막는다. 보이게 하려면 두 목록 중 하나에 적는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

SCOPED_TABLES = ("customers", "orders", "order_items", "shipments", "order_notes", "order_status_history")
SHARED_TABLES = ("products", "warehouses", "inventory")  # 거래처와 무관한 참조 데이터. 범위 사용자도 전체를 본다


@dataclass(frozen=True)
class Scope:
    customer_ids: tuple[int, ...] = field(default_factory=tuple)
    region: str | None = None

    @classmethod
    def parse(cls, spec: str | dict[str, Any] | None) -> "Scope | None":
        """'region=부산' | 'customers=1,5,9' | {'customer_ids': [...], 'region': '부산'}"""
        if not spec:
            return None
        if isinstance(spec, dict):
            ids = tuple(int(x) for x in spec.get("customer_ids") or ())
            return cls(customer_ids=ids, region=spec.get("region") or None)
        out: dict[str, Any] = {}
        for part in str(spec).split(";"):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k, v = k.strip(), v.strip()
            if k in ("customers", "customer_ids"):
                out["customer_ids"] = [int(x) for x in v.split(",") if x.strip()]
            elif k == "region":
                out["region"] = v
        return cls.parse(out) if out else None

    def to_dict(self) -> dict[str, Any]:
        return {"customer_ids": list(self.customer_ids), "region": self.region}

    def describe(self) -> str:
        parts = []
        if self.region:
            parts.append(f"{self.region} 지역 거래처")
        if self.customer_ids:
            parts.append("거래처 id " + ", ".join(map(str, self.customer_ids)))
        return " 및 ".join(parts) or "전체"

    def customer_where(self) -> tuple[str, tuple]:
        conds, params = [], []
        if self.region:
            conds.append("region = ?")
            params.append(self.region)
        if self.customer_ids:
            conds.append(f"id IN ({','.join('?' * len(self.customer_ids))})")
            params.extend(self.customer_ids)
        return (" AND ".join(conds) or "1=0"), tuple(params)


def apply_scope(conn: sqlite3.Connection, scope: Scope) -> None:
    """연결에 범위 뷰를 씌운다. 반드시 authorizer 설정 전에 호출(뷰 생성 권한 필요)."""
    where, params = scope.customer_where()
    # 파라미터를 뷰 정의에 직접 넣을 수 없으므로 안전하게 리터럴로 바꾼다(정수·간단 문자열만 허용)
    lit = []
    for p in params:
        if isinstance(p, int):
            lit.append(str(p))
        else:
            if any(c in str(p) for c in "';\\"):
                raise ValueError("범위 값에 허용되지 않는 문자")
            lit.append("'" + str(p) + "'")
    for l in lit:
        where = where.replace("?", l, 1)
    conn.executescript(f"""
        CREATE TEMP VIEW customers AS SELECT * FROM main.customers WHERE {where};
        CREATE TEMP VIEW orders AS SELECT * FROM main.orders WHERE customer_id IN (SELECT id FROM temp.customers);
        CREATE TEMP VIEW order_items AS SELECT * FROM main.order_items WHERE order_id IN (SELECT id FROM temp.orders);
        CREATE TEMP VIEW shipments AS SELECT * FROM main.shipments WHERE order_id IN (SELECT id FROM temp.orders);
        CREATE TEMP VIEW order_notes AS SELECT * FROM main.order_notes WHERE order_id IN (SELECT id FROM temp.orders);
        CREATE TEMP VIEW order_status_history AS SELECT * FROM main.order_status_history WHERE order_id IN (SELECT id FROM temp.orders);
    """)
    # 기본 거부: 두 목록에 없는 테이블은 빈 뷰. 범위 사용자가 run_sql 로 이름을 맞춰도 0행이다
    for (name,) in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'").fetchall():
        if name in SCOPED_TABLES or name in SHARED_TABLES or name.startswith("sqlite_") or '"' in name:
            continue
        conn.execute(f'CREATE TEMP VIEW "{name}" AS SELECT * FROM main."{name}" WHERE 0')
