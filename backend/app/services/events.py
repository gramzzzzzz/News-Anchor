"""In-process event bus feeding server-sent events (the app is a single process).
publish() is safe to call from worker threads."""
from __future__ import annotations

import asyncio
import json


class EventBus:
    def __init__(self):
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def _deliver(self, data: str) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                self._subs.discard(q)

    def publish(self, event: dict) -> None:
        data = json.dumps(event, default=str)
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._deliver, data)
