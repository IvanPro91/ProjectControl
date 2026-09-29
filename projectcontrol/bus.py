from __future__ import annotations

import asyncio
import itertools
import threading


class EventBus:
    """Fan-out of events from worker threads to websocket subscribers."""

    def __init__(self, maxsize: int = 4000):
        self._lock = threading.Lock()
        self._subs: dict[int, asyncio.Queue] = {}
        self._ids = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._maxsize = maxsize

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        with self._lock:
            sub_id = next(self._ids)
            self._subs[sub_id] = queue
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subs.pop(sub_id, None)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, event: dict) -> None:
        with self._lock:
            queues = list(self._subs.values())
        if not queues:
            return
        loop = self._loop
        for queue in queues:
            if loop is not None and loop.is_running():
                try:
                    loop.call_soon_threadsafe(self._put, queue, event)
                except RuntimeError:
                    pass
            else:
                self._put(queue, event)

    @staticmethod
    def _put(queue: asyncio.Queue, event: dict) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except Exception:
                pass
