"""긴 도구의 진행률 보고. 도구는 `report(done, total, label)` 만 부르고, 누가 받는지는 모른다.

에이전트 루프가 도구를 실행하는 동안 `progress_scope(cb)` 로 수신자를 걸어 두면(contextvar, 스레드별) 그 안의 report 가
cb 로 간다. 수신자가 없으면 아무 일도 하지 않으므로 CLI·MCP·테스트에서 도구를 그냥 불러도 안전하다.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

Reporter = Callable[[int, int, str], None]
_current: ContextVar[Reporter | None] = ContextVar("harness_progress", default=None)


def report(done: int, total: int, label: str = "") -> None:
    """지금까지 done/total 끝났다. label 은 사람이 읽을 한 줄(예: 'R1=2.15 kΩ')."""
    cb = _current.get()
    if cb is not None:
        try:
            cb(int(done), int(total), str(label))
        except Exception:
            pass  # 진행률 보고가 도구를 멈추게 해서는 안 된다


@contextmanager
def progress_scope(cb: Reporter | None) -> Iterator[None]:
    token = _current.set(cb)
    try:
        yield
    finally:
        _current.reset(token)
