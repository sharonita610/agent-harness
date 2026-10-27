"""읽기 전용 SQL 가드와 연결. 어떤 팩이든 SQLite 조회 도구를 만들 때 쓴다(enterprise, factory).

두 겹 방어: ① guard_select 가 SELECT/WITH 단일 문장만 통과시키고 LIMIT 을 강제, ② 연결 자체가 읽기 전용 + authorizer 로 변경을 거부.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum|reindex|begin|commit|rollback)\b", re.I)
_HAS_LIMIT = re.compile(r"\blimit\b\s+\d+", re.I)
_ALLOWED_AUTH = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}


def guard_select(sql: str, default_limit: int = 200, max_limit: int = 500, scoped: bool = False, write_hint: str = "변경은 전용 도구를 쓰세요.") -> str:
    """SELECT/WITH 단일 문장만 허용. 금지 키워드 차단. LIMIT 강제. scoped 면 main./temp. 스키마 명시도 차단."""
    s = sql.strip().rstrip(";").strip()
    if scoped and re.search(r"\b(main|temp)\s*\.", s, re.I):
        raise ValueError("테이블 이름에 스키마를 붙일 수 없습니다. 테이블 이름만 쓰세요.")
    if ";" in s:
        raise ValueError("문장은 하나만 허용됩니다(세미콜론 금지).")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise ValueError("SELECT 또는 WITH 로 시작하는 조회만 허용됩니다.")
    if _FORBIDDEN.search(s):
        raise ValueError("데이터를 변경하는 키워드는 run_sql 에서 쓸 수 없습니다. " + write_hint)
    m = _HAS_LIMIT.search(s)
    if not m:
        s += f" LIMIT {default_limit}"
    else:
        n = int(m.group(0).split()[-1])
        if n > max_limit:
            s = _HAS_LIMIT.sub(f"LIMIT {max_limit}", s)
    return s


def authorizer(action, arg1, arg2, dbname, source):
    return sqlite3.SQLITE_OK if action in _ALLOWED_AUTH else sqlite3.SQLITE_DENY


def connect_readonly(db_path: Path, setup: Callable[[sqlite3.Connection], None] | None = None) -> sqlite3.Connection:
    """읽기 전용 URI 연결 + authorizer. setup 은 authorizer 를 걸기 전에 실행된다(임시 뷰 등)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if setup is not None:
        setup(conn)
    conn.set_authorizer(authorizer)
    return conn


def rows(cur: sqlite3.Cursor, limit: int | None = None, mask: dict[str, Callable[[Any], Any]] | None = None) -> dict[str, Any]:
    cols = [d[0] for d in cur.description] if cur.description else []
    fetched = cur.fetchall() if limit is None else cur.fetchmany(limit + 1)
    truncated = limit is not None and len(fetched) > limit
    if truncated:
        fetched = fetched[:limit]
    out = []
    for r in fetched:
        row = dict(zip(cols, tuple(r)))
        for c, fn in (mask or {}).items():
            if c in row:
                row[c] = fn(row[c])
        out.append(row)
    return {"columns": cols, "rows": out, "row_count": len(out), "truncated": truncated}
