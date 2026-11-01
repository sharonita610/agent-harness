"""사용자별 데이터 범위(row-level): 같은 이름의 임시 뷰로 거래처·주문·배송·메모·이력을 걸러낸다.

SQLite 는 이름이 겹치면 temp 스키마를 먼저 찾으므로, 모델이 쓰는 임의의 SELECT 도 범위 밖 행을 볼 수 없다.
`main.orders` 처럼 스키마를 명시한 우회는 SQL 가드가 막는다(tools.guard_select).

기본 거부(ADR-033): 범위 뷰(SCOPED_TABLES)도 공용 참조 데이터(SHARED_TABLES)도 아닌 테이블은 범위 사용자에게 빈 뷰로 덮는다.
실데이터에 나중에 붙는 테이블(payments 같은)이 "아직 분류 안 됨" 상태로 전부 보이는 일을 막는다. 보이게 하려면 두 목록 중 하나에 적는다.

열 마스킹(ADR-035): 같은 뷰가 민감도 등급표(sensitivity.SENSITIVITY)의 열을 가리거나 뺀다. 범위가 없는 사용자에게도 적용된다.
뷰 하나가 행(범위)과 열(민감도)을 함께 다루므로 두 장치가 서로를 덮어쓰지 않는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping

from ...tools.sql import build_views
from .sensitivity import SENSITIVITY

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


def scope_where(scope: Scope) -> dict[str, str]:
    """범위 뷰의 WHERE 절(테이블별). 파라미터를 뷰 정의에 직접 넣을 수 없어 리터럴로 바꾼다(정수·간단 문자열만 허용)."""
    where, params = scope.customer_where()
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
    by_order = "order_id IN (SELECT id FROM temp.orders)"
    return {
        "customers": where,
        "orders": "customer_id IN (SELECT id FROM temp.customers)",
        "order_items": by_order,
        "shipments": by_order,
        "order_notes": by_order,
        "order_status_history": by_order,
    }


def apply_scope(
    conn: sqlite3.Connection, scope: Scope | None, policy: Mapping[str, Mapping[str, str]] | None = None
) -> list[str]:
    """연결에 뷰를 씌운다. 민감도 등급표(열 마스킹·비노출)는 모두에게, 범위(행 필터)와 기본 거부는 scope 가 있을 때.
    반드시 authorizer 설정 전에 호출(뷰 생성 권한 필요). 만든 뷰 이름을 돌려준다."""
    policy = SENSITIVITY if policy is None else policy
    if scope is None:
        return build_views(conn, policy=policy)
    return build_views(
        conn, policy=policy, where=scope_where(scope), hide_unlisted_except=SCOPED_TABLES + SHARED_TABLES
    )
