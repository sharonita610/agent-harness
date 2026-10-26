"""가짜 회사 "한빛상사" 데이터 생성. 시드 고정 → 매번 같은 DB.

사용: uv run harness seed   또는   uv run python -m harness.packs.enterprise.seed
기준일(REFERENCE_DATE)을 고정해 "지난달" 같은 상대 표현이 평가에서 흔들리지 않게 한다.
"""
from __future__ import annotations

import hashlib
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from faker import Faker

REFERENCE_DATE = date(2026, 10, 27)  # 에이전트에게 "오늘" 로 알려주는 날짜
SEED = 42
PACK_DIR = Path(__file__).parent
DEFAULT_DB = PACK_DIR.parents[2] / "data" / "enterprise.db"

STATUSES = ["접수", "출고", "배송중", "완료", "지연", "취소"]
DELAY_REASONS = ["재고 부족", "배송사 지연", "주소 오류", "결제 확인 지연"]
CATEGORIES = {
    "사무용품": ["복사용지 A4", "토너 카트리지", "바인더", "라벨지", "화이트보드 마커", "스테이플러"],
    "전자부품": ["릴레이 모듈", "전원 어댑터 12V", "케이블 타이", "커넥터 세트", "LED 모듈", "센서 보드"],
    "포장재": ["박스 소형", "박스 대형", "에어캡 롤", "테이프 48mm", "완충재", "파렛트 랩"],
    "공구": ["드릴 비트 세트", "토크 렌치", "절연 장갑", "니퍼", "줄자 5m", "레벨기"],
    "소모품": ["세정제 1L", "장갑 니트릴", "방진 마스크", "윤활유 스프레이", "청소포", "쓰레기봉투"],
}
CARRIERS = ["한빛택배", "대한물류", "퀵라인"]
REGIONS = ["서울", "경기", "인천", "부산", "대구", "대전", "광주"]
PAYMENT_TERMS = {"A": "월말 결제 (60일)", "B": "월말 결제 (30일)", "C": "선결제"}


def _d(day: date) -> str:
    return day.isoformat()


def build(db_path: Path = DEFAULT_DB) -> dict:
    rng = random.Random(SEED)
    fake = Faker("ko_KR")
    Faker.seed(SEED)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript((PACK_DIR / "schema.sql").read_text(encoding="utf-8"))
    cur = conn.cursor()

    # 거래처 50
    customers = []
    for i in range(1, 51):
        grade = rng.choices(["A", "B", "C"], weights=[20, 35, 45])[0]
        created = REFERENCE_DATE - timedelta(days=rng.randint(200, 1500))
        customers.append((i, fake.company(), grade, rng.choice(REGIONS), fake.name(), fake.phone_number(), PAYMENT_TERMS[grade], _d(created)))
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)", customers)

    # 상품 200
    products = []
    pid = 1
    for cat, names in CATEGORIES.items():
        for n in range(40):
            base = names[n % len(names)]
            name = f"{base} {['표준', '고급', '대용량', '에코', '프로'][n // len(names) % 5]}"
            price = rng.choice([1200, 2500, 4800, 7500, 12000, 18000, 25000, 42000, 68000, 95000, 150000])
            products.append((pid, f"{cat[:2]}-{pid:04d}", name, cat, price))
            pid += 1
    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)

    # 창고 2, 재고 400
    cur.executemany("INSERT INTO warehouses VALUES (?,?,?)", [(1, "수도권 1창고", "경기"), (2, "남부 2창고", "부산")])
    inv = []
    iid = 1
    for wid in (1, 2):
        for p in products:
            safety = rng.choice([20, 50, 100, 200])
            on_hand = max(0, int(rng.gauss(safety * 1.6, safety * 0.9)))
            if rng.random() < 0.12:
                on_hand = rng.randint(0, safety - 1)  # 안전재고 미달
            inv.append((iid, wid, p[0], on_hand, safety, _d(REFERENCE_DATE - timedelta(days=rng.randint(0, 6)))))
            iid += 1
    cur.executemany("INSERT INTO inventory VALUES (?,?,?,?,?,?)", inv)

    # 주문 3000 (최근 365일), 주문상품, 배송, 상태 이력
    orders, items, ships, hist = [], [], [], []
    item_id = ship_id = hist_id = 1
    for oid in range(1, 3001):
        cust = rng.choice(customers)
        odate = REFERENCE_DATE - timedelta(days=rng.randint(0, 364))
        age = (REFERENCE_DATE - odate).days
        if age < 3:
            status = rng.choices(["접수", "출고", "지연"], weights=[60, 30, 10])[0]
        elif age < 10:
            status = rng.choices(["출고", "배송중", "완료", "지연", "취소"], weights=[20, 30, 30, 15, 5])[0]
        else:
            status = rng.choices(["완료", "지연", "취소"], weights=[86, 8, 6])[0]
        delay_reason = rng.choices(DELAY_REASONS, weights=[45, 30, 15, 10])[0] if status == "지연" else None
        discount = 0.03 if cust[2] == "A" else 0.0
        if rng.random() < 0.08:
            discount = rng.choice([0.05, 0.1, 0.15])
        n_items = rng.randint(1, 5)
        total = 0
        for p in rng.sample(products, n_items):
            qty = rng.randint(1, 40)
            items.append((item_id, oid, p[0], qty, p[4]))
            total += qty * p[4]
            item_id += 1
        total = int(round(total * (1 - discount)))
        orders.append((oid, cust[0], _d(odate), status, total, delay_reason, discount))
        hist.append((hist_id, oid, None, "접수", None, "system", _d(odate)))
        hist_id += 1
        if status in ("출고", "배송중", "완료", "지연"):
            wid = 1 if cust[3] in ("서울", "경기", "인천", "대전") else 2
            planned = odate + timedelta(days=rng.randint(2, 4))
            if status == "지연":
                shipped = planned + timedelta(days=rng.randint(2, 14)) if rng.random() < 0.6 else None
                delivered = None
            else:
                shipped = planned + timedelta(days=rng.choice([0, 0, 0, 1]))
                delivered = shipped + timedelta(days=rng.randint(1, 3)) if status in ("완료",) else None
            ships.append((ship_id, oid, wid, rng.choice(CARRIERS), _d(planned), _d(shipped) if shipped else None, _d(delivered) if delivered else None))
            ship_id += 1
            if status != "지연" or shipped:
                hist.append((hist_id, oid, "접수", "출고", None, "system", _d(shipped or planned)))
                hist_id += 1
            if status == "지연":
                hist.append((hist_id, oid, "접수", "지연", delay_reason, "system", _d(planned + timedelta(days=1))))
                hist_id += 1
        if status == "취소":
            hist.append((hist_id, oid, "접수", "취소", rng.choice(["고객 요청", "결제 실패"]), "cs_team", _d(odate + timedelta(days=rng.randint(0, 3)))))
            hist_id += 1
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?)", orders)
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)
    cur.executemany("INSERT INTO shipments VALUES (?,?,?,?,?,?,?)", ships)
    cur.executemany("INSERT INTO order_status_history VALUES (?,?,?,?,?,?,?)", hist)

    # 메모 몇 개
    notes = []
    for nid, oid in enumerate(rng.sample(range(1, 3001), 120), start=1):
        notes.append((nid, oid, rng.choice(["cs_kim", "sales_park", "wh_lee"]), rng.choice(["고객 재확인 요청", "분할 출고 안내 완료", "대체품 제안 수락", "연락 불가, 재시도 예정"]), _d(REFERENCE_DATE - timedelta(days=rng.randint(0, 60)))))
    cur.executemany("INSERT INTO order_notes VALUES (?,?,?,?,?)", notes)
    conn.commit()

    counts = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ["customers", "products", "warehouses", "inventory", "orders", "order_items", "shipments", "order_notes", "order_status_history"]}
    digest = hashlib.sha256("\n".join(line for line in conn.iterdump()).encode()).hexdigest()[:16]
    conn.close()
    return {"db": str(db_path), "counts": counts, "sha256_16": digest, "reference_date": _d(REFERENCE_DATE)}


if __name__ == "__main__":
    info = build()
    for k, v in info.items():
        print(f"{k}: {v}")
