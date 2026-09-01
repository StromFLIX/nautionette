"""In-process fan-out so every client sees the system working."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._history: list[dict[str, Any]] = []
        self._history_size = history

    def publish(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {"kind": kind, "at": time.time(), **(payload or {})}
        self._history.append(event)
        del self._history[: max(0, len(self._history) - self._history_size)]
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._subscribers.discard(queue)
        return event

    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    async def stream(self):
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        try:
            yield sse({"kind": "connected", "at": time.time()})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield sse(event)
        finally:
            self._subscribers.discard(queue)


def sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, default=str)}\n\n"


bus = EventBus()
