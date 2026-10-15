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
_chat_turns: set[asyncio.Task[Any]] = set()
_draining = False


def _finished(task: asyncio.Task[Any]) -> None:
    _running.discard(task)
    _chat_turns.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        log.error("background task %s failed: %s", task.get_name(), error)


def spawn(coro: Coroutine[Any, Any, Any], name: str, *, chat_turn: bool = False) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro, name=name)
    _running.add(task)
    if chat_turn:
        _chat_turns.add(task)
    task.add_done_callback(_finished)
    return task


def begin_draining() -> None:
    """Fence chat admission/queue advancement before waiting for active turns."""
    global _draining
    _draining = True


def is_draining() -> bool:
    return _draining


async def wait_for_chat_turns() -> None:
    """Wait without cancelling turns if this waiter times out or is cancelled."""
    if _chat_turns:
        await asyncio.wait(_chat_turns)


async def drain() -> None:
    """Cancel remaining work, after the server has given chat turns time to finish."""
    for task in list(_running):
        task.cancel()
    if _running:
        await asyncio.gather(*_running, return_exceptions=True)
