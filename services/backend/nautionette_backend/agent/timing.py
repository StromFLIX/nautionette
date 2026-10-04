"""Observed response phases; concurrent tools count once, not once per call."""

from __future__ import annotations

import time
from typing import Any


class ActivityTiming:
    def __init__(self) -> None:
        self.totals = dict.fromkeys(("tools", "thinking", "reply", "model", "tool_input", "other"), 0.0)
        self.phase = "other"
        self.model_phase = "other"
        self.explicit_phases = False
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
        if kind == "phase" and event.get("phase") in {"thinking", "reply", "model", "tool_input", "other"}:
            self.explicit_phases = True
            self.model_phase = event["phase"]
        elif kind == "thinking":
            self.model_phase = "thinking"
        elif kind == "delta":
            self.model_phase = "reply"
        elif kind in {"usage", "agent_end"}:
            self.model_phase = "other"
        elif not self.explicit_phases and kind in {"tool", "tool_done", "status"}:
            # Legacy agents have no block boundaries. Do not extend their last
            # text/reasoning delta into unobserved time. With boundaries, tools
            # override model activity without erasing the underlying phase.
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
