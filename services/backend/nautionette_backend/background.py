"""Work that outlives the request that started it.

asyncio only keeps a weak reference to a running task, so anything nobody awaits
has to be held here or it can be collected part way through.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger("nautionette")

_running: set[asyncio.Task[Any]] = set()


def _finished(task: asyncio.Task[Any]) -> None:
    _running.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        log.error("background task %s failed: %s", task.get_name(), error)


def spawn(coro: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro, name=name)
    _running.add(task)
    task.add_done_callback(_finished)
    return task


async def drain() -> None:
    """Stop everything still in flight, so shutdown is not held open by a poll."""
    for task in list(_running):
        task.cancel()
    if _running:
        await asyncio.gather(*_running, return_exceptions=True)
