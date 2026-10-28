"""사용자별 데이터 범위: 데이터 층(임시 뷰)에서 강제되는지."""

import json

import pytest
from fastapi.testclient import TestClient

from harness.agent.types import LLMResponse, ToolCall
from harness.packs.enterprise.scope import Scope
from harness.packs.enterprise.tools import SYSTEM_PROMPT, build_tools, scope_note
from harness.service.app import create_app
from harness.service.auth import UserStore
from harness.tools.registry import ToolRegistry
from tests.test_service import ScriptProvider, parse_sse


def test_scope_parse_and_describe():
    assert Scope.parse("region=부산").describe() == "부산 지역 거래처"
    s = Scope.parse("customers=1,5,9;region=서울")
    assert s.customer_ids == (1, 5, 9) and s.region == "서울" and "거래처 id 1, 5, 9" in s.describe()
    assert Scope.parse(None) is None and Scope.parse({}) is None
    assert Scope.parse({"customer_ids": [3], "region": None}).to_dict() == {"customer_ids": [3], "region": None}
    with pytest.raises(ValueError):
        build_tools_scope_injection()


def build_tools_scope_injection():
    import sqlite3

    from harness.packs.enterprise.scope import apply_scope

    apply_scope(sqlite3.connect(":memory:"), Scope(region="부산' OR '1'='1"))


def test_scoped_registry_filters_every_scoped_table(seeded_db):
    db, _ = seeded_db
    full = ToolRegistry(build_tools(db))
    busan = ToolRegistry(build_tools(db, scope=Scope.parse("region=부산")))
    cnt = lambda reg, sql: reg.get("run_sql").run({"sql": sql})["rows"][0]["n"]
    assert cnt(busan, "SELECT COUNT(*) n FROM customers") < cnt(full, "SELECT COUNT(*) n FROM customers")
    assert cnt(busan, "SELECT COUNT(*) n FROM customers WHERE region!='부산'") == 0
    assert cnt(busan, "SELECT COUNT(*) n FROM orders") < cnt(full, "SELECT COUNT(*) n FROM orders")
    # 조인·서브쿼리도 범위 안에서만
    assert (
        cnt(
            busan,
            "SELECT COUNT(*) n FROM shipments s JOIN orders o ON o.id=s.order_id JOIN customers c ON c.id=o.customer_id WHERE c.region!='부산'",
        )
        == 0
    )
    assert cnt(busan, "SELECT COUNT(*) n FROM inventory") == cnt(
        full, "SELECT COUNT(*) n FROM inventory"
    )  # 공통 테이블은 그대로
    with pytest.raises(ValueError):  # 스키마 명시 우회 차단
        busan.get("run_sql").run({"sql": "SELECT COUNT(*) FROM main.orders"})
    out = full.get("run_sql").run(
        {
            "sql": "SELECT o.id FROM orders o JOIN customers c ON c.id=o.customer_id WHERE c.region!='부산' AND o.status='접수' LIMIT 1"
        }
    )["rows"][0]["id"]
    with pytest.raises(ValueError):
        busan.get("get_order").run({"order_id": out})
    with pytest.raises(ValueError, match="범위"):
        busan.get("create_note").run({"order_id": out, "note": "범위 밖 메모"})
    with pytest.raises(ValueError, match="범위"):
        busan.get("update_order_status").run({"order_id": out, "new_status": "출고", "reason": "테스트"})
    assert "부산 지역 거래처" in scope_note(Scope.parse("region=부산")) and scope_note(None) == ""


def test_service_applies_user_scope(tmp_path, seeded_db):
    db, _ = seeded_db
    us = UserStore(path=tmp_path / "users.json")
    k_all = us.add("본사", "operator")
    k_busan = us.add("부산담당", "operator", scope={"region": "부산"})
    script = lambda: [
        LLMResponse(
            tool_calls=[
                ToolCall(id="c1", name="run_sql", arguments={"sql": "SELECT COUNT(*) AS n FROM orders", "limit": 1})
            ]
        ),
        LLMResponse(text="끝"),
    ]
    from harness.packs.enterprise.tools import make_index

    idx = make_index(hybrid=False)
    app = create_app(
        db_path=tmp_path / "svc.db",
        provider_factory=lambda spec: ScriptProvider(script()),
        registry=ToolRegistry(build_tools(db, index=idx)),
        system_prompt=SYSTEM_PROMPT,
        users=us,
        kwargs_registry_factory=lambda sc: ToolRegistry(build_tools(db, index=idx, scope=sc)),
    )
    with TestClient(app) as c:
        me = c.get("/me", headers={"Authorization": f"Bearer {k_busan}"}).json()
        assert me["scope"] == {"customer_ids": [], "region": "부산"} and me["scope_text"] == "부산 지역 거래처"
        counts = {}
        for name, key in (("all", k_all), ("busan", k_busan)):
            with c.stream(
                "POST", "/chat", json={"message": "주문 수"}, headers={"Authorization": f"Bearer {key}"}
            ) as r:
                ev = dict(parse_sse(r.iter_lines()))
            counts[name] = (
                json.loads(ev["tool_result"]["preview"])["rows"][0]["n"]
                if ev["tool_result"]["preview"].startswith("{")
                else None
            )
        assert counts["busan"] < counts["all"] and counts["all"] == 3000
