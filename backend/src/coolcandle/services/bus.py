"""In-process event bus. The WebSocket hub, the buddy and alerts all listen here."""

import asyncio
import contextlib
from typing import Any

QUEUE_SIZE = 2000


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        event = {"type": event_type, "data": data}
        for queue in self._subscribers:
            if queue.full():  # a slow browser loses its oldest update, never blocks trading
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)
