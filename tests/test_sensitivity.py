"""열 민감도 등급표와 뷰 단계 마스킹(ADR-035): 어떤 SQL 로도 원문이 나가지 않는다."""

from __future__ import annotations

import re
import shutil
import sqlite3

import pytest

from harness.packs.enterprise.scope import Scope
from harness.packs.enterprise.tools import build_tools
from harness.tools.registry import ToolRegistry
from harness.tools.sql import build_views, masked_select_list

RAW_PHONE = re.compile(r"\b0\d{1,2}-\d{3,4}-\d{4}\b")  # 가려지지 않은 번호 꼴

BYPASS_ATTEMPTS = [
    "SELECT name, phone FROM customers",
    "SELECT phone AS tel FROM customers",  # 별칭: 예전 방식(출력 열 이름만 봄)은 여기서 새었다
    "SELECT substr(phone, 1, 12) AS p FROM customers",  # 함수
    "SELECT c.phone AS contact FROM customers c JOIN orders o ON o.customer_id = c.id",  # 조인
    "SELECT group_concat(phone) AS all_phones FROM customers",  # 묶음
    "SELECT * FROM customers",
    "WITH x AS (SELECT phone AS v FROM customers) SELECT v FROM x",  # CTE
    "SELECT (SELECT phone FROM customers WHERE id = 1) AS v",  # 서브쿼리
]


def _strings(out: dict) -> list[str]:
    return [v for r in out["rows"] for v in r.values() if isinstance(v, str)]


@pytest.mark.parametrize("sql", BYPASS_ATTEMPTS)
def test_mask_survives_alias_function_join_and_group_concat(seeded_db, sql):
    db, _ = seeded_db
    out = ToolRegistry(build_tools(db)).get("run_sql").run({"sql": sql, "limit": 5})
    values = _strings(out)
    assert values, sql
    assert not any(RAW_PHONE.search(v) for v in values), (sql, values[:2])
    assert any("****" in v for v in values), (sql, values[:2])


def test_mask_shape_and_schema_bypass_blocked(seeded_db):
    db, _ = seeded_db
    reg = ToolRegistry(build_tools(db))
    raw = sqlite3.connect(db).execute("SELECT phone FROM customers WHERE id = 1").fetchone()[0]
    masked = reg.get("run_sql").run({"sql": "SELECT phone FROM customers WHERE id = 1"})["rows"][0]["phone"]
    prefix = raw.split("-", 1)[0]
    assert masked == f"{prefix}-****-{raw[-4:]}" and masked != raw  # 국번 + **** + 뒤 4자리
    with pytest.raises(ValueError, match="스키마"):  # 범위가 없어도 뷰가 있으면 main. 명시는 막는다
        reg.get("run_sql").run({"sql": "SELECT phone FROM main.customers"})
    cols = reg.get("describe_table").run({"table": "customers"})["columns"]
    by_name = {c["name"]: c for c in cols}
    assert "마스킹" in by_name["phone"]["sensitivity"] and "sensitivity" not in by_name["name"]


def test_scope_and_mask_compose(seeded_db):
    db, _ = seeded_db
    busan = ToolRegistry(build_tools(db, scope=Scope.parse("region=부산")))
    out = busan.get("run_sql").run({"sql": "SELECT region, phone AS tel FROM customers"})
    assert out["rows"] and all(r["region"] == "부산" and "****" in r["tel"] for r in out["rows"])
    assert busan.get("run_sql").run({"sql": "SELECT COUNT(*) n FROM products"})["rows"][0]["n"] > 0  # 공용은 그대로


def test_policy_grades_email_full_drop_and_describe_labels(seeded_db, tmp_path):
    db, _ = seeded_db
    copy = tmp_path / "with_pii.db"
    shutil.copy(db, copy)
    with sqlite3.connect(copy) as c:
        c.execute("ALTER TABLE customers ADD COLUMN email TEXT")
        c.execute("ALTER TABLE customers ADD COLUMN resident_no TEXT")
        c.execute("UPDATE customers SET email = 'kim@example.com', resident_no = '900101-1234567'")
    policy = {"customers": {"phone": "phone", "email": "email", "payment_terms": "full", "resident_no": "drop"}}
    reg = ToolRegistry(build_tools(copy, sensitivity=policy))
    row = reg.get("run_sql").run({"sql": "SELECT email, payment_terms, phone FROM customers LIMIT 1"})["rows"][0]
    assert row["email"] == "***@example.com" and row["payment_terms"] == "***" and "****" in row["phone"]
    star = reg.get("run_sql").run({"sql": "SELECT * FROM customers LIMIT 1"})["rows"][0]
    assert "resident_no" not in star  # drop 열은 뷰에 없다
    with pytest.raises(ValueError, match="no such column"):
        reg.get("run_sql").run({"sql": "SELECT resident_no FROM customers"})
    labels = {c["name"]: c.get("sensitivity") for c in reg.get("describe_table").run({"table": "customers"})["columns"]}
    assert "비노출" in labels["resident_no"] and "이메일" in labels["email"] and "전체" in labels["payment_terms"]
    assert "900101" not in str(reg.get("get_customer").run({"customer_id": 1}))  # 전용 도구도 같은 연결


def test_policy_typos_fail_loudly(seeded_db):
    db, _ = seeded_db
    with pytest.raises(ValueError, match="테이블이 DB 에 없습니다"):
        build_tools(db, sensitivity={"customer": {"phone": "phone"}})
    with pytest.raises(ValueError, match="열이 customers 에 없습니다"):
        build_tools(db, sensitivity={"customers": {"mobile": "phone"}})
    with pytest.raises(ValueError, match="알 수 없는 민감도 등급"):
        masked_select_list(["phone"], {"phone": "hide"})
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(a TEXT, b TEXT)")
    assert build_views(conn, policy={}) == []  # 등급표가 비면 뷰도 없다(공정 팩)
    assert build_views(conn, policy={"t": {"a": "full"}}) == ["t"]
    conn.execute("INSERT INTO main.t VALUES ('x', 'y')")
    assert conn.execute("SELECT a, b FROM t").fetchone() == ("***", "y")
