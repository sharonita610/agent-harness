"""승인 브로커: 에이전트 스레드가 변경 도구 실행 전에 사람 결정을 기다리고, API 가 결정을 넣어준다."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

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

    def wait(self, aid: str, should_cancel: Callable[[], bool] | None = None) -> bool:
        """에이전트 스레드에서 호출. 결정이 올 때까지(최대 timeout_s) 막는다. should_cancel 이 참이 되면 전표를 취소로 닫고 False."""
        ev = self._events.get(aid)
        if ev is None:
            return False
        deadline = time.monotonic() + self.timeout_s
        decided = False
        while not decided:
            decided = ev.wait(0.5)
            if decided:
                break
            if should_cancel and should_cancel():
                self._close(aid, "cancelled", "cancelled")
                break
            if time.monotonic() >= deadline:
                self._close(aid, "expired", "timeout")
                break
        with self._lock:
            return self._decisions.get(aid, False)

    def _close(self, aid: str, status: str, by: str) -> None:
        with self._lock:
            if aid not in self._decisions:
                self.store.decide_approval(aid, status, by)
                self._decisions[aid] = False
                ev = self._events.get(aid)
                if ev:
                    ev.set()
                self._prune_locked()

    def cancel_pending(self, run_id: str, by: str = "cancelled") -> int:
        """실행이 취소되면 그 실행의 대기 전표를 닫는다. 결재함에 남아 나중에 승인되는 일을 막는다."""
        n = 0
        for a in self.store.list_approvals("pending"):
            if a["run_id"] == run_id:
                self._close(a["id"], "cancelled", by)
                n += 1
        return n

    def decide(self, aid: str, approve: bool, by: str = "api") -> bool:
        with self._lock:
            ev = self._events.get(aid)
            if ev is None or aid in self._decisions:
                return False
            self._decisions[aid] = approve
            self.store.decide_approval(aid, "approved" if approve else "denied", by)
            ev.set()
            self._prune_locked()
            return True

    MAX_DECIDED = 1000  # 처리된 전표는 이 수를 넘기면 오래된 것부터 메모리에서 지운다(저장소에는 남는다)

    def _prune_locked(self) -> None:
        """결정이 끝난 전표의 이벤트·결정을 오래된 순으로 지운다. 대기 중인 전표는 건드리지 않는다. _lock 안에서 부른다."""
        if len(self._decisions) <= self.MAX_DECIDED:
            return
        drop = len(self._decisions) - self.MAX_DECIDED // 2
        for aid in list(self._decisions)[:drop]:
            self._decisions.pop(aid, None)
            self._events.pop(aid, None)

    def memory_size(self) -> dict[str, int]:
        with self._lock:
            return {"events": len(self._events), "decisions": len(self._decisions)}

    def pending(self) -> list[dict[str, Any]]:
        return self.store.list_approvals("pending")
