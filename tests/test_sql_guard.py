import sqlite3

import pytest

from harness.packs.enterprise.tools import connect_ro, guard_select


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
