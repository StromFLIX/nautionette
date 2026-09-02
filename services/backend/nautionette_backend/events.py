"""In-process fan-out so every client sees the system working."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from .db import db


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._history: list[dict[str, Any]] = []
        self._history_size = history

    def publish(self, kind: str, payload: dict[str, Any] | None = None, scope: str = "app") -> dict[str, Any]:
        event = {"kind": kind, "at": time.time(), **(payload or {})}
        self._history.append(event)
        del self._history[: max(0, len(self._history) - self._history_size)]
        # Kept as well as broadcast, so a client that arrives after a restart
        # still sees what happened.
        with contextlib.suppress(Exception):
            db.add_event(scope, kind, payload or {})
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._subscribers.discard(queue)
        return event

    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """What has happened, from storage, in the shape a subscriber receives."""
        return [
            {"kind": row["kind"], "at": row["created_at"], **row["payload"]}
            for row in db.recent_events(limit)
        ]

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
