import pytest

from harness.packs.enterprise.tools import build_tools
from harness.tools.registry import ToolRegistry


@pytest.fixture
def reg(seeded_db):
    db, _ = seeded_db
    return ToolRegistry(build_tools(db))


def test_run_sql_masks_phone(reg):
    out = reg.get("run_sql").run({"sql": "SELECT name, phone FROM customers", "limit": 3})
    assert out["row_count"] == 3
    assert all("****" in r["phone"] for r in out["rows"])
    assert out["sql_executed"].endswith("LIMIT 3")


def test_get_order_and_customer(reg):
    o = reg.get("get_order").run({"order_id": 1})
    assert o["order"]["id"] == 1 and "customer_name" in o["order"]
    c = reg.get("get_customer").run({"customer_id": 1})
    assert "****" in c["customer"]["phone"]
    with pytest.raises(ValueError):
        reg.get("get_order").run({"order_id": 999999})


def test_update_status_rules(reg, seeded_db):
    db, _ = seeded_db
    import sqlite3

    conn = sqlite3.connect(db)
    done = conn.execute("SELECT id FROM orders WHERE status='완료' LIMIT 1").fetchone()[0]
    pending = conn.execute("SELECT id FROM orders WHERE status='접수' LIMIT 1").fetchone()[0]
    conn.close()
    with pytest.raises(ValueError):  # 완료는 변경 불가
        reg.get("update_order_status").run({"order_id": done, "new_status": "취소", "reason": "고객 요청"})
    with pytest.raises(ValueError):  # 취소 사유 제한
        reg.get("update_order_status").run({"order_id": pending, "new_status": "취소", "reason": "그냥"})
    res = reg.get("update_order_status").run({"order_id": pending, "new_status": "출고", "reason": "재고 확보"})
    assert res["before"] == "접수" and res["after"] == "출고"
    hist = reg.get("get_order").run({"order_id": pending})["history"]
    assert hist[-1]["to_status"] == "출고" and "approved" in hist[-1]["changed_by"]
