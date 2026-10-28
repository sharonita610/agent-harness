"""승인 브로커: 에이전트 스레드가 변경 도구 실행 전에 사람 결정을 기다리고, API 가 결정을 넣어준다."""

from __future__ import annotations

import threading
from typing import Any

from .store import Store


class ApprovalBroker:
    def __init__(self, store: Store, timeout_s: float = 600):
        self.store = store
        self.timeout_s = timeout_s
        self._events: dict[str, threading.Event] = {}
        self._decisions: dict[str, bool] = {}
        self._lock = threading.Lock()

    def request(self, run_id: str, tool: str, args: dict[str, Any], tier: str) -> str:
        aid = self.store.create_approval(run_id, tool, args, tier)
        with self._lock:
            self._events[aid] = threading.Event()
        return aid

    def wait(self, aid: str) -> bool:
        """에이전트 스레드에서 호출. 결정이 올 때까지(최대 timeout_s) 막는다."""
        ev = self._events.get(aid)
        if ev is None:
            return False
        if not ev.wait(self.timeout_s):
            with self._lock:
                if aid not in self._decisions:
                    self.store.decide_approval(aid, "expired", "timeout")
                    self._decisions[aid] = False
        with self._lock:
            return self._decisions.get(aid, False)

    def decide(self, aid: str, approve: bool, by: str = "api") -> bool:
        with self._lock:
            ev = self._events.get(aid)
            if ev is None or aid in self._decisions:
                return False
            self._decisions[aid] = approve
            self.store.decide_approval(aid, "approved" if approve else "denied", by)
            ev.set()
            return True

    def pending(self) -> list[dict[str, Any]]:
        return self.store.list_approvals("pending")
