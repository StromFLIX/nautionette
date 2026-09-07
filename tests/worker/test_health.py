"""A running container is ready only with fresh, successfully loaded workflows."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from nautionette_worker import health


@pytest.fixture
def ready_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "STATE_PATH", tmp_path / "health.json")
    directory = tmp_path / "workflows"
    directory.mkdir()
    (directory / "daily.py").write_text("workflow source")
    state = {
        "at": time.time(),
        "sources": health.sources(str(directory)),
        "files": [{"file": "daily.py", "workflows": ["Daily"], "error": None}],
    }
    health.STATE_PATH.write_text(json.dumps(state))
    return directory, state


def test_ready_worker_reports_its_loaded_files(ready_worker):
    directory, _state = ready_worker
    assert health.check(str(directory)) == {"status": "ready", "files": ["daily.py"]}


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_workflow_changes_require_a_reload(ready_worker, change):
    directory, _state = ready_worker
    if change == "edit":
        (directory / "daily.py").write_text("new workflow source")
    elif change == "add":
        (directory / "hourly.py").write_text("another workflow source")
    else:
        (directory / "daily.py").unlink()
    result = health.check(str(directory))
    assert result["status"] == "degraded"
    assert "changed" in result["error"]


@pytest.mark.parametrize("failure", ["stale", "load_error", "no_workflow", "missing", "invalid"])
def test_unready_workers_fail_the_probe(ready_worker, failure):
    directory, state = ready_worker
    if failure == "stale":
        state["at"] -= health.STALE_SECONDS + 1
    elif failure == "load_error":
        state["files"][0]["error"] = "ImportError: missing dependency"
    elif failure == "no_workflow":
        state["files"][0]["workflows"] = []
    health.STATE_PATH.write_text(json.dumps(state))
    if failure == "missing":
        health.clear()
    elif failure == "invalid":
        health.STATE_PATH.write_text("not json")
    assert health.check(str(directory))["status"] == "degraded"


async def test_heartbeat_publishes_readiness_and_stops(ready_worker, monkeypatch):
    directory, state = ready_worker
    stopping = asyncio.Event()

    async def stop_after_publish(*_args, **_kwargs):
        assert health.check(str(directory))["status"] == "ready"
        stopping.set()
        await _args[0]

    monkeypatch.setattr(asyncio, "wait_for", stop_after_publish)
    health.clear()
    await health.maintain(stopping, state["files"], state["sources"])
    assert stopping.is_set()
    health.clear()
    assert not health.STATE_PATH.exists()
