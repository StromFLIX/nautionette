"""Readiness of the running worker and its loaded workflow sources."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from .loader import _workflow_files

STATE_PATH = Path("/home/app/.nautionette-worker-health.json")
HEARTBEAT_SECONDS = 10
STALE_SECONDS = 30


def sources(directory: str) -> dict[str, str]:
    return {
        file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in _workflow_files(Path(directory))
    }


def clear() -> None:
    STATE_PATH.unlink(missing_ok=True)


async def maintain(stopping: asyncio.Event, report: list[dict], loaded_sources: dict[str, str]) -> None:
    while not stopping.is_set():
        state = {"at": time.time(), "files": report, "sources": loaded_sources}
        temporary = STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(STATE_PATH)
        try:
            await asyncio.wait_for(stopping.wait(), timeout=HEARTBEAT_SECONDS)
        except TimeoutError:
            pass


def check(directory: str) -> dict[str, Any]:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if time.time() - state["at"] > STALE_SECONDS:
            raise ValueError("worker heartbeat is stale")
        if state["sources"] != sources(directory):
            raise ValueError("workflow files changed since this worker loaded them")
        failed = [item["file"] for item in state["files"] if item["error"] or not item["workflows"]]
        if failed:
            raise ValueError(f"workflows not loaded: {', '.join(failed)}")
        return {"status": "ready", "files": [item["file"] for item in state["files"]]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "degraded", "error": str(exc)[:500]}


if __name__ == "__main__":
    result = check(os.environ.get("WORKFLOWS_DIR", "/workflows"))
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "ready" else 1)
