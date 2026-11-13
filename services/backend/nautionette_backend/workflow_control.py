"""Serialize schedule controls and starts within the single backend process."""

from __future__ import annotations

import asyncio
from weakref import WeakValueDictionary

_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def workflow_lock(name: str) -> asyncio.Lock:
    # Callers retain the lock throughout their async-with, including waiters.
    lock = _locks.get(name)
    if lock is None:
        lock = _locks[name] = asyncio.Lock()
    return lock
