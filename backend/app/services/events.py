"""In-process event bus for live run streaming.

One backend instance (free tier), so a simple in-memory fan-out is correct.
Subscribers get a bounded queue; a slow browser can never grow memory without
limit because the oldest events are dropped first.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self, max_queue: int = 256) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._max_queue = max_queue
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subscribers[channel].add(queue)
        return queue

    async def unsubscribe(self, channel: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(channel)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(channel, None)

    async def publish(self, channel: str, event: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(channel, ()))
        for queue in queues:
            if queue.full():
                # Drop the oldest event rather than growing memory for a slow reader.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - defensive
                logger.debug("dropping event for a full subscriber queue")

    def subscriber_count(self, channel: str) -> int:
        return len(self._subscribers.get(channel, ()))


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def set_bus(bus: EventBus | None) -> None:
    global _bus
    _bus = bus
