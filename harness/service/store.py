"""서비스 저장소(SQLite): 세션·메시지·실행(run)·이벤트·승인. 스레드 안전(RLock), WAL."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created_at TEXT, provider TEXT, title TEXT);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, session_id TEXT, kind TEXT, question TEXT, status TEXT,
  answer TEXT, stopped_reason TEXT, steps INTEGER, latency_s REAL, prompt_tokens INTEGER, completion_tokens INTEGER,
  error TEXT, created_at TEXT, started_at TEXT, finished_at TEXT);
CREATE TABLE IF NOT EXISTS run_events(id INTEGER PRIMARY KEY, run_id TEXT, seq INTEGER, kind TEXT, step INTEGER, data TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, run_id TEXT, tool TEXT, args TEXT, tier TEXT, status TEXT,
  created_at TEXT, decided_at TEXT, decided_by TEXT);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, user TEXT, role TEXT, action TEXT, target TEXT, detail TEXT, ip TEXT);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def _q(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            self._conn.commit()
            return rows

    # ---- sessions
    def create_session(self, provider: str, title: str | None = None) -> dict[str, Any]:
        sid = new_id("sess")
        self._q("INSERT INTO sessions VALUES (?,?,?,?)", (sid, now(), provider, title))
        return self.get_session(sid)  # type: ignore[return-value]

    def get_session(self, sid: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM sessions WHERE id=?", (sid,))
        if not rows:
            return None
        s = rows[0]
        s["messages"] = self._q("SELECT role, content, created_at FROM messages WHERE session_id=? ORDER BY id", (sid,))
        return s

    def add_message(self, sid: str, role: str, content: str) -> None:
        self._q(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES (?,?,?,?)", (sid, role, content, now())
        )

    def history(self, sid: str, limit: int = 10) -> list[dict[str, str]]:
        rows = self._q("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (sid, limit))
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    # ---- runs
    def create_run(self, sid: str, kind: str, question: str) -> str:
        rid = new_id("run")
        self._q(
            "INSERT INTO runs(id, session_id, kind, question, status, created_at) VALUES (?,?,?,?,?,?)",
            (rid, sid, kind, question, "queued", now()),
        )
        return rid

    def start_run(self, rid: str) -> None:
        self._q("UPDATE runs SET status='running', started_at=? WHERE id=?", (now(), rid))

    def finish_run(self, rid: str, status: str, result=None, error: str | None = None) -> None:
        if result is not None:
            self._q(
                "UPDATE runs SET status=?, answer=?, stopped_reason=?, steps=?, latency_s=?, prompt_tokens=?, completion_tokens=?, error=?, finished_at=? WHERE id=?",
                (
                    status,
                    result.answer,
                    result.stopped_reason,
                    result.steps,
                    result.latency_s,
                    result.usage.prompt_tokens,
                    result.usage.completion_tokens,
                    error,
                    now(),
                    rid,
                ),
            )
        else:
            self._q("UPDATE runs SET status=?, error=?, finished_at=? WHERE id=?", (status, error, now(), rid))

    def fail_stale(self, reason: str = "서버 재시작으로 중단됨") -> int:
        """프로세스가 죽을 때 running/queued 로 남은 실행과 pending 결재를 정리한다(시작 시 호출)."""
        n = len(self._q("SELECT id FROM runs WHERE status IN ('running','queued')"))
        self._q(
            "UPDATE runs SET status='failed', error=?, finished_at=? WHERE status IN ('running','queued')",
            (reason, now()),
        )
        self._q(
            "UPDATE approvals SET status='expired', decided_at=?, decided_by='restart' WHERE status='pending'", (now(),)
        )
        return n

    def get_run(self, rid: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM runs WHERE id=?", (rid,))
        return rows[0] if rows else None

    def list_runs(self, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        if kind:
            return self._q("SELECT * FROM runs WHERE kind=? ORDER BY created_at DESC LIMIT ?", (kind, limit))
        return self._q("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))

    def add_event(self, rid: str, seq: int, kind: str, step: int | None, data: dict[str, Any]) -> None:
        self._q(
            "INSERT INTO run_events(run_id, seq, kind, step, data, ts) VALUES (?,?,?,?,?,?)",
            (rid, seq, kind, step, json.dumps(data, ensure_ascii=False, default=str), now()),
        )

    def events(self, rid: str, after_seq: int = 0) -> list[dict[str, Any]]:
        rows = self._q(
            "SELECT seq, kind, step, data, ts FROM run_events WHERE run_id=? AND seq>? ORDER BY seq", (rid, after_seq)
        )
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    # ---- audit
    def add_audit(
        self,
        user: str,
        role: str,
        action: str,
        target: str,
        detail: dict[str, Any] | None = None,
        ip: str | None = None,
    ) -> None:
        self._q(
            "INSERT INTO audit(ts, user, role, action, target, detail, ip) VALUES (?,?,?,?,?,?,?)",
            (now(), user, role, action, target, json.dumps(detail or {}, ensure_ascii=False, default=str), ip),
        )

    def list_audit(self, limit: int = 100, user: str | None = None) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM audit WHERE (? IS NULL OR user=?) ORDER BY id DESC LIMIT ?", (user, user, limit))
        for r in rows:
            r["detail"] = json.loads(r["detail"])
        return rows

    # ---- approvals
    def create_approval(self, rid: str, tool: str, args: dict[str, Any], tier: str) -> str:
        aid = new_id("apr")
        self._q(
            "INSERT INTO approvals(id, run_id, tool, args, tier, status, created_at) VALUES (?,?,?,?,?,?,?)",
            (aid, rid, tool, json.dumps(args, ensure_ascii=False, default=str), tier, "pending", now()),
        )
        return aid

    def decide_approval(self, aid: str, status: str, by: str) -> None:
        self._q("UPDATE approvals SET status=?, decided_at=?, decided_by=? WHERE id=?", (status, now(), by, aid))

    def get_approval(self, aid: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM approvals WHERE id=?", (aid,))
        if not rows:
            return None
        a = rows[0]
        a["args"] = json.loads(a["args"])
        return a

    def list_approvals(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._q(
            "SELECT * FROM approvals WHERE (? IS NULL OR status=?) ORDER BY created_at DESC LIMIT ?",
            (status, status, limit),
        )
        for r in rows:
            r["args"] = json.loads(r["args"])
        return rows
