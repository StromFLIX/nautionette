"""Observed response phases; concurrent tools count once, not once per call."""

from __future__ import annotations

import time
from typing import Any


class ActivityTiming:
    def __init__(self) -> None:
        self.totals = dict.fromkeys(("tools", "thinking", "reply", "other"), 0.0)
        self.phase = "other"
        self.model_phase = "other"
        self.updated = time.monotonic()
        self.stopped = False

    def _advance(self) -> None:
        now = time.monotonic()
        if not self.stopped:
            self.totals[self.phase] += max(0, now - self.updated) * 1000
        self.updated = now

    def observe(self, event: dict[str, Any], tools_running: bool) -> None:
        if self.stopped:
            return
        self._advance()
        kind = event.get("type")
        if kind == "phase" and event.get("phase") in {"thinking", "reply", "other"}:
            self.model_phase = event["phase"]
        elif kind == "thinking":
            self.model_phase = "thinking"
        elif kind == "delta":
            self.model_phase = "reply"
        elif kind in {"tool", "tool_done", "usage", "status", "agent_end"}:
            self.model_phase = "other"
        self.phase = "tools" if tools_running else self.model_phase
        if kind in {"result", "error", "interrupted"}:
            self.stopped = True

    def snapshot(self, *, final: bool = False) -> dict[str, Any]:
        self._advance()
        return {
            **{f"{key}_ms": round(value) for key, value in self.totals.items()},
            "active": None if final or self.stopped else self.phase,
            "updated_at": time.time(),
        }

    def finish(self) -> dict[str, Any]:
        snapshot = self.snapshot(final=True)
        self.stopped = True
        return snapshot
