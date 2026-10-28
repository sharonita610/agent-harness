"""가짜 공장 데이터 시드(결정적). 회사 이름 없음.

- 라인 4개 × 스테이션 6개(전극 코팅 → 슬리팅 → 권취 → 조립 → 전해액 주입 → 화성), 스테이션마다 설비 1대
- 기준일 2026-10-27 기준 14일(10-14 ~ 10-27), 시간별 검사 수량과 NG 수
- NG 이벤트 1건 = 이미지 1장(파일명 끝 _ng), 분류기 제안 라벨·확신도(모의 비전 모델)
- 심어 둔 이상 3개:
  A) 2라인 3스테이션(권취) 설비 2026-10-18 09:00~14:00 NG 율 5배 — 10-18 08:00 정비 직후 temp_c +8 (근본 원인 후보)
  B) 4라인 5스테이션(전해액 주입) 야간 02:00~04:00 매일 '이물' NG 2.5배 — 교대 패턴
  C) 로트 L1-0931(1라인, 10-22) 전체 '정렬 불량' 집중 — 자재 배치 M-7781
사용: uv run harness seed --pack factory
"""

from __future__ import annotations

import hashlib
import os
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 7
REFERENCE_DATE = date(2026, 10, 27)
DAYS = 14
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = Path(os.getenv("HARNESS_FACTORY_DB", str(ROOT / "data" / "factory.db")))
SCHEMA = Path(__file__).parent / "schema.sql"

PROCESSES = [
    ("전극 코팅", "coating"),
    ("슬리팅", "slitting"),
    ("권취", "winding"),
    ("조립", "assembly"),
    ("전해액 주입", "filling"),
    ("화성", "formation"),
]
DEFECTS = ["스크래치", "이물", "두께 편차", "정렬 불량", "기포", "탭 손상"]
# 공정별로 흔한 불량(가중치)
DEFECT_W = {
    "coating": [3, 2, 5, 1, 2, 0],
    "slitting": [5, 2, 2, 1, 0, 1],
    "winding": [2, 2, 1, 5, 0, 2],
    "assembly": [1, 2, 0, 3, 0, 5],
    "filling": [0, 5, 0, 0, 4, 1],
    "formation": [1, 1, 2, 0, 4, 1],
}
PARAM_BASE = {
    "coating": {"temp_c": 120.0, "speed_mpm": 30.0},
    "slitting": {"tension_n": 45.0, "speed_mpm": 60.0},
    "winding": {"temp_c": 24.0, "tension_n": 12.0},
    "assembly": {"pressure_kpa": 210.0, "speed_mpm": 18.0},
    "filling": {"temp_c": 22.0, "pressure_kpa": 95.0},
    "formation": {"temp_c": 45.0, "pressure_kpa": 101.0},
}


def _ts(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def build(db_path: Path = DEFAULT_DB) -> dict:
    rng = random.Random(SEED)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    start = datetime.combine(REFERENCE_DATE - timedelta(days=DAYS - 1), datetime.min.time())

    # 라인·스테이션·설비
    for li in range(1, 5):
        conn.execute(
            "INSERT INTO lines VALUES (?,?,?,?)", (li, f"{li}라인", "파우치 셀" if li <= 2 else "원통형 셀", "3교대")
        )
        for si, (name, proc) in enumerate(PROCESSES, start=1):
            sid = li * 10 + si
            conn.execute("INSERT INTO stations VALUES (?,?,?,?,?)", (sid, li, si, name, proc))
            eid = sid
            installed = start - timedelta(days=rng.randint(200, 900))
            last_m = start - timedelta(days=rng.randint(5, 40))
            conn.execute(
                "INSERT INTO equipment VALUES (?,?,?,?,?,?)",
                (
                    eid,
                    sid,
                    f"EQ-{li}{si:02d}",
                    f"{proc.upper()}-{rng.choice(['A2', 'B1', 'C3'])}",
                    installed.strftime("%Y-%m-%d"),
                    last_m.strftime("%Y-%m-%d"),
                ),
            )
    # 심은 이상 A 의 정비 기록: 2라인 권취(23) 설비, 10-18 08:00
    anomaly_a_eq = 23
    conn.execute("UPDATE equipment SET last_maintenance_at='2026-10-18' WHERE id=?", (anomaly_a_eq,))
    conn.execute(
        "INSERT INTO maintenance(equipment_id, ts, kind, note) VALUES (?,?,?,?)",
        (anomaly_a_eq, "2026-10-18 08:00:00", "히터 교체", "권취 예열 히터 모듈 교체, 설정값 재입력"),
    )
    for eid, when, kind in (
        (11, "2026-10-15 06:00:00", "정기 점검"),
        (34, "2026-10-20 13:00:00", "노즐 세척"),
        (46, "2026-10-24 05:30:00", "센서 교정"),
    ):
        conn.execute(
            "INSERT INTO maintenance(equipment_id, ts, kind, note) VALUES (?,?,?,?)", (eid, when, kind, "정상 완료")
        )

    # 로트: 라인당 하루 3개
    lots: dict[tuple[int, int], list[int]] = {}
    lot_id = 0
    special_lot = None
    for day in range(DAYS):
        d = start + timedelta(days=day)
        for li in range(1, 5):
            for k in range(3):
                lot_id += 1
                started = d + timedelta(hours=8 * k)
                batch = f"M-{rng.randint(7000, 7999)}"
                if li == 1 and d.date() == date(2026, 10, 22) and k == 1:
                    special_lot = lot_id
                    batch = "M-7781"
                conn.execute(
                    "INSERT INTO lots VALUES (?,?,?,?,?)", (lot_id, li, _ts(started), rng.randint(1800, 2400), batch)
                )
                lots.setdefault((li, day), []).append(lot_id)

    # 시간별 검사·NG, 파라미터, NG 이벤트
    ng_id = 0
    base_rate = {
        "coating": 0.012,
        "slitting": 0.010,
        "winding": 0.014,
        "assembly": 0.011,
        "filling": 0.009,
        "formation": 0.008,
    }
    for day in range(DAYS):
        d = start + timedelta(days=day)
        for li in range(1, 5):
            for si, (name, proc) in enumerate(PROCESSES, start=1):
                sid = li * 10 + si
                eid = sid
                for h in range(24):
                    t = d + timedelta(hours=h)
                    inspected = rng.randint(280, 360) if 6 <= h < 22 else rng.randint(180, 240)
                    rate = base_rate[proc] * rng.uniform(0.75, 1.25)
                    params = dict(PARAM_BASE[proc])
                    hot_defect = None
                    # 이상 A: 2라인 권취, 10-18 09~13시
                    if sid == 23 and t.date() == date(2026, 10, 18) and 9 <= h < 14:
                        rate *= 5.0
                        hot_defect = "정렬 불량"
                    if eid == anomaly_a_eq and t >= datetime(2026, 10, 18, 8):
                        params["temp_c"] += (
                            8.0 if t < datetime(2026, 10, 18, 14) else 1.0
                        )  # 14시 설정값 재조정 후 거의 정상
                    # 이상 B: 4라인 전해액 주입 야간 02~04시 이물
                    if sid == 45 and 2 <= h < 4:
                        rate *= 2.5
                        hot_defect = "이물"
                    ng = int(round(inspected * rate))
                    # 이상 C: 특정 로트(1라인 10-22 2번째 로트, 08~16시) 정렬 불량
                    lot_now = lots[(li, day)][min(2, h // 8)]
                    if lot_now == special_lot and si in (3, 4):
                        ng += int(inspected * 0.03)
                        hot_defect = "정렬 불량"
                    conn.execute(
                        "INSERT INTO inspections(hour_start, line_id, station_id, inspected, ng) VALUES (?,?,?,?,?)",
                        (_ts(t), li, sid, inspected, ng),
                    )
                    for p, base in params.items():
                        conn.execute(
                            "INSERT INTO param_logs(ts, equipment_id, param, value) VALUES (?,?,?,?)",
                            (_ts(t), eid, p, round(base * rng.uniform(0.985, 1.015), 2)),
                        )
                    weights = DEFECT_W[proc]
                    for _ in range(ng):
                        ng_id += 1
                        minute = rng.randint(0, 59)
                        ev_t = t + timedelta(minutes=minute, seconds=rng.randint(0, 59))
                        if hot_defect and rng.random() < 0.7:
                            dt = hot_defect
                        else:
                            dt = rng.choices(DEFECTS, weights=weights)[0]
                        conf = round(rng.uniform(0.55, 0.98), 2)
                        suggested = dt if rng.random() < 0.85 else rng.choice(DEFECTS)
                        status = "suggested"
                        confirmed, by, at = None, None, None
                        if day < DAYS - 3 and rng.random() < 0.6:  # 오래된 건 엔지니어가 확정한 비율
                            status, confirmed, by, at = (
                                "confirmed",
                                dt,
                                rng.choice(["김엔지니어", "이엔지니어", "박엔지니어"]),
                                _ts(ev_t + timedelta(hours=rng.randint(6, 48))),
                            )
                        img = f"/data/ng/{ev_t.strftime('%Y-%m-%d')}/L{li}_S{si}_{ev_t.strftime('%Y%m%d_%H%M%S')}_{ng_id:06d}_ng.jpg"
                        conn.execute(
                            "INSERT INTO ng_events(ts, line_id, station_id, equipment_id, lot_id, image_path, defect_type, suggested_label, suggested_confidence, label_status, confirmed_label, labeled_by, labeled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                _ts(ev_t),
                                li,
                                sid,
                                eid,
                                lot_now,
                                img,
                                dt if status == "confirmed" else None,
                                suggested,
                                conf,
                                status,
                                confirmed,
                                by,
                                at,
                            ),
                        )
    # 과거 조치(효과 비교용): 3라인 슬리팅 장력 재설정 10-19, 적용 후 NG 감소를 데이터에 반영
    conn.execute(
        "INSERT INTO actions(created_at, line_id, station_id, equipment_id, title, description, owner, status, applied_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "2026-10-18 15:00:00",
            3,
            32,
            32,
            "슬리팅 장력 재설정",
            "장력 45N → 42N, 가이드 롤러 정렬",
            "박엔지니어",
            "done",
            "2026-10-19 09:00:00",
            "박엔지니어",
        ),
    )
    conn.execute(
        "UPDATE inspections SET ng = CAST(ng * 0.6 AS INTEGER) WHERE station_id=32 AND hour_start >= '2026-10-19 09:00:00'"
    )
    conn.execute("DELETE FROM ng_events WHERE station_id=32 AND ts >= '2026-10-19 09:00:00' AND id % 5 IN (0, 1)")
    conn.execute(
        "INSERT INTO actions(created_at, line_id, station_id, equipment_id, title, description, owner, status, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "2026-10-25 10:00:00",
            4,
            45,
            45,
            "야간 주입 노즐 이물 점검",
            "02~04시 이물 NG 반복, 야간 교대 세척 주기 확인",
            "이엔지니어",
            "open",
            "이엔지니어",
        ),
    )
    for sid in (23, 45):
        conn.execute(
            "INSERT INTO alert_rules(station_id, threshold_multiplier, window_hours, enabled, updated_at, updated_by) VALUES (?,?,?,?,?,?)",
            (sid, 3.0, 1, 1, "2026-10-14 00:00:00", "시스템"),
        )
    conn.commit()
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "lines",
            "stations",
            "equipment",
            "lots",
            "inspections",
            "ng_events",
            "param_logs",
            "maintenance",
            "actions",
            "alert_rules",
        )
    }
    digest = hashlib.sha256("\n".join(conn.iterdump()).encode()).hexdigest()[:16]
    conn.close()
    return {
        "db": str(db_path),
        "counts": counts,
        "sha256_16": digest,
        "reference_date": REFERENCE_DATE.isoformat(),
        "special_lot": special_lot,
    }


if __name__ == "__main__":
    print(build())
