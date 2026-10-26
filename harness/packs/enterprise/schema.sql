-- 회사 데이터 비서 샘플 DB 스키마 (가짜 회사 "한빛상사")
CREATE TABLE customers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  grade TEXT NOT NULL CHECK (grade IN ('A','B','C')),
  region TEXT NOT NULL,
  manager TEXT NOT NULL,          -- 거래처 담당자 이름
  phone TEXT NOT NULL,            -- 담당자 연락처 (개인정보)
  payment_terms TEXT NOT NULL,    -- 결제 조건
  created_at TEXT NOT NULL
);
CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  unit_price INTEGER NOT NULL     -- 원
);
CREATE TABLE warehouses (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  region TEXT NOT NULL
);
CREATE TABLE inventory (
  id INTEGER PRIMARY KEY,
  warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  on_hand INTEGER NOT NULL,
  safety_stock INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (warehouse_id, product_id)
);
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  order_date TEXT NOT NULL,       -- YYYY-MM-DD
  status TEXT NOT NULL CHECK (status IN ('접수','출고','배송중','완료','지연','취소')),
  total_amount INTEGER NOT NULL,  -- 원, order_items 합계
  delay_reason TEXT,              -- 지연 상태일 때만: 재고 부족 / 배송사 지연 / 주소 오류 / 결제 확인 지연
  discount_rate REAL NOT NULL DEFAULT 0
);
CREATE TABLE order_items (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL,
  unit_price INTEGER NOT NULL
);
CREATE TABLE shipments (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
  carrier TEXT NOT NULL,
  planned_date TEXT NOT NULL,     -- 출고 예정일
  shipped_date TEXT,              -- 실제 출고일
  delivered_date TEXT
);
CREATE TABLE order_notes (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  author TEXT NOT NULL,
  note TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE order_status_history (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  from_status TEXT,
  to_status TEXT NOT NULL,
  reason TEXT,
  changed_by TEXT NOT NULL,
  changed_at TEXT NOT NULL
);
CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_ship_order ON shipments(order_id);
