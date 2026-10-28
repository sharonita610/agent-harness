"""비동기 잡 큐: 오래 걸리는 질문을 큐에 넣고 워커가 순서대로 처리한다. 상태는 저장소(runs) 에, 취소는 협조적."""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from .runner import execute_run, make_sink


class JobQueue:
    def __init__(self, state, concurrency: int = 1):
        self.state = state
        self.concurrency = concurrency
        self._queue: asyncio.Queue = asyncio.Queue()
        self._cancel: dict[str, threading.Event] = {}
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._tasks = [asyncio.create_task(self._worker(i)) for i in range(self.concurrency)]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    def submit(self, session_id: str, question: str, provider_spec: str | None, user=None) -> str:
        run_id = self.state.store.create_run(session_id, "job", question)
        self._cancel[run_id] = threading.Event()
        item = (run_id, session_id, question, provider_spec, user)
        loop = getattr(self, "_loop", None)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop:
            loop.call_soon_threadsafe(self._queue.put_nowait, item)  # 스레드풀(def 엔드포인트·A2A blocking)에서 넣어도 워커가 깨어난다
        else:
            self._queue.put_nowait(item)
        return run_id

    def cancel(self, run_id: str) -> bool:
        flag = self._cancel.get(run_id)
        run = self.state.store.get_run(run_id)
        if flag is None or run is None or run["status"] in ("done", "failed", "cancelled"):
            return False
        flag.set()
        if run["status"] == "queued":
            self.state.store.finish_run(run_id, "cancelled", error="큐에서 취소됨")
        return True

    def status(self, run_id: str) -> dict[str, Any] | None:
        return self.state.store.get_run(run_id)

    async def _worker(self, idx: int) -> None:
        loop = asyncio.get_running_loop()
        while True:
            run_id, session_id, question, provider_spec, user = await self._queue.get()
            flag = self._cancel.get(run_id)
            if flag is not None and flag.is_set():
                self._queue.task_done()
                continue
            sink = make_sink(self.state.store, run_id)
            try:
                await loop.run_in_executor(None, execute_run, self.state, run_id, session_id, question, provider_spec, sink,
                                           flag.is_set if flag else None, user)
            except Exception:
                pass  # execute_run 이 failed 로 기록함
            finally:
                self._queue.task_done()
