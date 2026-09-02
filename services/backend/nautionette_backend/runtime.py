"""State the app works out at runtime rather than being told.

The environment sets the floor for every setting; anything saved in the app wins
over it. The catalog cache and the model windows live here too, because both the
catalog and the things that invalidate it need them, and neither should import
the other.
"""

from __future__ import annotations

import time
from typing import Any

from .agent import DEFAULT_HISTORY_CHARS
from .config import settings
from .db import db

# Rough but stable: a token is about four characters of English.
CHARS_PER_TOKEN = 4
# Half the window for transcript, leaving room for the system prompt, the new
# turn, tool schemas and the answer itself.
HISTORY_SHARE = 0.5

CATALOG_TTL = 60.0

# Context length per model id, refreshed whenever the catalog is built.
model_windows: dict[str, int] = {}

_catalog_cache: dict[str, Any] = {"at": 0.0, "value": None}

# Flipped by the first agent call that comes back clean, so the status page can
# say "a model answered" without anyone configuring a second flag.
_agent_answered = False


def defaults() -> dict[str, Any]:
    return {
        "default_model": settings.agent_model,
        "default_agent_set": settings.default_agent_set,
        # 0 means "work it out from the model", which is what you want by default.
        "history_chars": 0,
    }


def runtime(key: str) -> Any:
    return db.get_setting(key, defaults()[key])


def history_budget(model: str | None) -> int:
    """How much transcript this model can actually be handed."""
    override = int(runtime("history_chars") or 0)
    if override > 0:
        return override
    window = model_windows.get(model or runtime("default_model"))
    if window:
        return int(window * CHARS_PER_TOKEN * HISTORY_SHARE)
    return DEFAULT_HISTORY_CHARS


def context_hints() -> dict[str, Any]:
    """The clients work the budget out per model with the same arithmetic."""
    return {
        "chars_per_token": CHARS_PER_TOKEN,
        "history_share": HISTORY_SHARE,
        "override": int(runtime("history_chars") or 0),
        "fallback": DEFAULT_HISTORY_CHARS,
    }


# ------------------------------------------------------------- catalog cache


def cached_catalog() -> dict[str, Any] | None:
    value = _catalog_cache["value"]
    if value and time.time() - _catalog_cache["at"] < CATALOG_TTL:
        return value
    return None


def cache_catalog(value: dict[str, Any]) -> dict[str, Any]:
    _catalog_cache.update(at=time.time(), value=value)
    return value


def forget_catalog() -> None:
    _catalog_cache.update(at=0.0, value=None)


# --------------------------------------------------------- has a model answered


def remember_agent_result(ok: bool) -> None:
    global _agent_answered
    if ok:
        _agent_answered = True


def agent_has_answered() -> bool:
    return _agent_answered
