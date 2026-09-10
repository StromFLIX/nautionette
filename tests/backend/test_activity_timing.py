from types import SimpleNamespace

import pytest
from nautionette_backend import conversations
from nautionette_backend.agent import runner, timing
from nautionette_backend.db import Database


@pytest.fixture
def clock(monkeypatch):
    value = [0.0]
    fake = SimpleNamespace(monotonic=lambda: value[0], time=lambda: 1000 + value[0])
    monkeypatch.setattr(runner, "time", fake)
    monkeypatch.setattr(timing, "time", fake)
    return value


def test_phases_and_parallel_tools_use_elapsed_not_summed_time(clock):
    timeline = runner.Timeline()

    def event(at, **payload):
        clock[0] = at
        if payload["type"] == "tool":
            timeline.start_tool(payload)
        elif payload["type"] == "tool_done":
            timeline.finish_tool(payload)
        timeline.observe(payload)

    event(2, type="phase", phase="thinking")
    event(5, type="phase", phase="other")
    event(6, type="tool", id="a", name="bash")
    event(7, type="tool", id="b", name="read")
    event(9, type="tool_done", id="a", result="ok")
    event(11, type="tool_done", id="b", error=True, result="failed")
    event(12, type="phase", phase="reply")
    event(14, type="phase", phase="other")
    event(15, type="result", ok=True)
    snapshot = timeline.timing.snapshot()
    assert snapshot == {
        "tools_ms": 5000,
        "thinking_ms": 3000,
        "reply_ms": 2000,
        "other_ms": 5000,
        "active": None,
        "updated_at": 1015,
    }
    assert [step["duration_ms"] for step in timeline.steps] == [3000, 4000]
    assert timeline.steps[1]["ok"] is False
    clock[0] = 999
    assert timeline.timing.finish()["tools_ms"] == 5000
    assert timeline.timing.finish()["other_ms"] == 5000


@pytest.mark.parametrize("terminal", ["result", "error", "interrupted"])
def test_termination_freezes_incomplete_tools_without_faking_success(clock, terminal):
    timeline = runner.Timeline()
    timeline.start_tool({"id": "pending", "name": "bash"})
    timeline.observe({"type": "tool"})
    clock[0] = 3
    timeline.observe({"type": terminal})
    step = timeline.steps[0]
    assert step["ok"] is None
    assert step["interrupted"] is True
    assert step["duration_ms"] == 3000
    assert step["finished_at"] == 1003
    clock[0] = 100
    timeline.stop_tools()
    assert step["duration_ms"] == 3000
    assert timeline.timing.finish()["tools_ms"] == 3000


def test_missing_start_is_not_a_zero_length_tool(clock):
    timeline = runner.Timeline()
    timeline.finish_tool({"id": "missing", "result": "ok"})
    assert "duration_ms" not in timeline.steps[0]
    assert "started_at" not in timeline.steps[0]
    assert timeline.steps[0]["ok"] is True
    assert not timeline._tool_started


def test_older_agent_phase_fallback_never_labels_idle_time_as_thinking(clock):
    tracker = timing.ActivityTiming()
    clock[0] = 10
    tracker.observe({"type": "thinking", "text": "not stored in timing"}, False)
    clock[0] = 12
    tracker.observe({"type": "usage"}, False)
    clock[0] = 15
    snapshot = tracker.finish()
    assert snapshot["thinking_ms"] == 2000
    assert snapshot["other_ms"] == 13000
    assert "text" not in snapshot


def test_steering_snapshot_does_not_stop_the_current_clock_on_duplicate_ack(clock):
    tracker = timing.ActivityTiming()
    clock[0] = 1
    assert tracker.snapshot(final=True)["active"] is None
    clock[0] = 3
    assert tracker.snapshot()["other_ms"] == 3000
    assert tracker.snapshot()["active"] == "other"


def test_timing_survives_reload_steering_and_restart_without_counting_downtime(tmp_path, monkeypatch):
    path = str(tmp_path / "chat.db")
    db = Database(path)
    chat = db.create_chat("Timings", "default")["id"]
    db.accept_chat_message(chat, "Run", "active")
    db.accept_chat_message(chat, "Next", "next", queue=True)
    steps = [{"kind": "tool", "id": "t", "name": "bash", "ok": True, "duration_ms": 1234}]
    first = {"tools_ms": 1234, "other_ms": 100, "active": None, "updated_at": 10}
    db.record_chat_progress("active", {"type": "tool_done"}, steps, "", first)
    db = Database(path)
    assert db.chat_snapshot(chat)["active_turn"]["timing"] == first
    assert db.consume_chat_input("active", "next", "First", {"steps": steps, "timing": first})
    assert db.chat_snapshot(chat)["active_turn"]["timing"] is None
    assert db.list_messages(chat)[1]["meta"]["timing"] == first
    second = {"thinking_ms": 500, "other_ms": 10, "active": "thinking", "updated_at": 20}
    db.record_chat_progress("active", {"type": "thinking"}, [], "", second)
    monkeypatch.setattr(conversations, "db", db)
    conversations.recover_interrupted()
    final = db.list_messages(chat)[-1]["meta"]["timing"]
    assert final == {**second, "active": None, "partial": True}
    assert db.list_messages(chat)[1]["meta"]["timing"] == first


async def test_run_persists_live_and_final_timing_and_tool_duration(backend, monkeypatch):
    chat = backend.db.create_chat("Timings", "default")["id"]
    backend.db.accept_chat_message(chat, "Run", "active")

    async def agent(job):
        yield {"type": "phase", "phase": "thinking"}
        snapshot = backend.db.chat_snapshot(chat)
        assert snapshot["active_turn"]["timing"]["active"] == "thinking"
        yield {"type": "tool", "id": "t", "name": "bash"}
        snapshot = backend.db.chat_snapshot(chat)
        assert snapshot["active_turn"]["timing"]["active"] == "tools"
        assert snapshot["active_turn"]["steps"][0]["started_at"] > 0
        yield {"type": "tool_done", "id": "t", "result": "ok"}
        yield {"type": "delta", "text": "Done"}
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("active", chat, {"prompt": "Run"})
    snapshot = backend.db.chat_snapshot(chat)
    assert snapshot["active_turn"] is None
    meta = snapshot["messages"][-1]["meta"]
    assert meta["timing"]["active"] is None
    assert meta["steps"][0]["duration_ms"] >= 0
    assert meta["steps"][0]["finished_at"] >= meta["steps"][0]["started_at"]
