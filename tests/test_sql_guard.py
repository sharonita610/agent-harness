import sqlite3

import pytest

from harness.packs.enterprise.tools import connect_ro, guard_select
from harness.tools.sql import strip_literals


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE orders SET status='취소'",
        "DELETE FROM orders",
        "SELECT 1; DROP TABLE orders",
        "PRAGMA table_info(orders)",
        "INSERT INTO orders VALUES (1)",
        "ATTACH DATABASE 'x' AS y",
        "select * from orders; update orders set status='x'",
    ],
)
def test_guard_blocks_writes(sql):
    with pytest.raises(ValueError):
        guard_select(sql)


def test_guard_appends_limit_and_caps():
    assert guard_select("SELECT * FROM orders").endswith("LIMIT 200")
    assert guard_select("SELECT * FROM orders LIMIT 10").endswith("LIMIT 10")
    assert guard_select("SELECT * FROM orders LIMIT 99999", max_limit=500).endswith("LIMIT 500")
    assert guard_select("WITH t AS (SELECT 1) SELECT * FROM t").startswith("WITH")


def test_readonly_connection_rejects_writes_even_without_guard(seeded_db):
    db, _ = seeded_db
    conn = connect_ro(db)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE orders SET status='취소' WHERE id=1")
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3000


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM orders WHERE note LIKE '%update%'",
        "SELECT id FROM order_notes WHERE note LIKE '%삭제%' OR note LIKE '%delete%'",
        "SELECT id FROM order_notes WHERE note = 'a; drop table x'",
        "SELECT id FROM orders WHERE reason = 'main.orders 참고'",
        'SELECT id FROM orders WHERE reason = "insert 요청"',
    ],
)
def test_guard_ignores_keywords_inside_string_literals(sql):
    """ADR-033: 메모 검색의 'update'·'delete'·';' 는 데이터다. 가드는 리터럴을 비운 본문만 본다."""
    assert guard_select(sql, scoped=True).startswith("SELECT")


def test_guard_still_blocks_keywords_outside_literals_and_broken_quotes():
    with pytest.raises(ValueError):
        guard_select("SELECT 1 FROM orders WHERE x = 'a'; DELETE FROM orders")
    with pytest.raises(ValueError):  # 닫히지 않은 따옴표 뒤의 키워드는 리터럴로 치지 않는다(보수적)
        guard_select("SELECT 1 FROM orders WHERE x = 'a; DELETE FROM orders")
    assert strip_literals("x = 'it''s' AND y = \"q\"") == "x = '' AND y = \"\""


def test_escaped_quote_literal_runs_as_one_select_on_readonly_connection(seeded_db):
    """'' 로 이어진 리터럴은 가드도 SQLite 도 한 문자열로 읽는다. 뒤의 DELETE 는 글자일 뿐이고 연결은 읽기 전용이다."""
    db, _ = seeded_db
    sql = "SELECT COUNT(*) AS n FROM orders WHERE status = ''' ; DELETE FROM orders; --'"
    conn = connect_ro(db)
    assert conn.execute(guard_select(sql)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3000
