-- 가짜 배터리 셀 공장. 회사 이름 없음. 라인 → 스테이션(공정) → 설비. 검사 결과 중 NG 는 이미지로 저장되고 파일명에 _ng 가 붙는다.
CREATE TABLE lines(id INTEGER PRIMARY KEY, name TEXT NOT NULL, product TEXT NOT NULL, shift_pattern TEXT NOT NULL);
CREATE TABLE stations(id INTEGER PRIMARY KEY, line_id INTEGER NOT NULL REFERENCES lines(id), seq INTEGER NOT NULL, name TEXT NOT NULL, process TEXT NOT NULL);
CREATE TABLE equipment(id INTEGER PRIMARY KEY, station_id INTEGER NOT NULL REFERENCES stations(id), code TEXT NOT NULL, model TEXT NOT NULL,
                       installed_at TEXT NOT NULL, last_maintenance_at TEXT);
CREATE TABLE lots(id INTEGER PRIMARY KEY, line_id INTEGER NOT NULL REFERENCES lines(id), started_at TEXT NOT NULL, cell_count INTEGER NOT NULL,
                  material_batch TEXT NOT NULL);
-- 시간별 검사 수량(NG 율의 분모)
CREATE TABLE inspections(id INTEGER PRIMARY KEY, hour_start TEXT NOT NULL, line_id INTEGER NOT NULL, station_id INTEGER NOT NULL,
                         inspected INTEGER NOT NULL, ng INTEGER NOT NULL);
-- NG 이벤트 1건 = 이미지 1장. image_path 파일명 끝에 _ng. label_status: unlabeled | suggested | confirmed
CREATE TABLE ng_events(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, line_id INTEGER NOT NULL, station_id INTEGER NOT NULL, equipment_id INTEGER NOT NULL,
                       lot_id INTEGER NOT NULL, image_path TEXT NOT NULL, defect_type TEXT, suggested_label TEXT, suggested_confidence REAL,
                       label_status TEXT NOT NULL DEFAULT 'unlabeled', confirmed_label TEXT, labeled_by TEXT, labeled_at TEXT);
-- 설비 파라미터(시간별): temp_c, tension_n, speed_mpm, pressure_kpa
CREATE TABLE param_logs(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, equipment_id INTEGER NOT NULL, param TEXT NOT NULL, value REAL NOT NULL);
CREATE TABLE maintenance(id INTEGER PRIMARY KEY, equipment_id INTEGER NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL, note TEXT);
-- 개선 조치(티켓). status: open | in_progress | done
CREATE TABLE actions(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, line_id INTEGER NOT NULL, station_id INTEGER NOT NULL, equipment_id INTEGER,
                     title TEXT NOT NULL, description TEXT, owner TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', applied_at TEXT, created_by TEXT);
CREATE TABLE alert_rules(id INTEGER PRIMARY KEY, station_id INTEGER NOT NULL, threshold_multiplier REAL NOT NULL, window_hours INTEGER NOT NULL,
                         enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT, updated_by TEXT);
CREATE INDEX ng_ts ON ng_events(ts);
CREATE INDEX ng_station ON ng_events(station_id, ts);
CREATE INDEX insp_station ON inspections(station_id, hour_start);
CREATE INDEX param_eq ON param_logs(equipment_id, ts);
