"""Tiny thread-safe publish/subscribe used to push job updates to SSE clients."""

from __future__ import annotations

import queue
import threading
from typing import Any

Event = dict[str, Any]


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[Event]] = set()

    def subscribe(self) -> queue.Queue[Event]:
        q: queue.Queue[Event] = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue[Event]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:  # a stuck client must not block the pipeline
                pass
