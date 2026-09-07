"""No-op runs retain their audit trail without changing any conversation."""

from __future__ import annotations

import pytest
from nautionette_backend import runs
from nautionette_backend.events import bus


@pytest.mark.parametrize("result", [None, "", {"notify": False, "reason": "already deployed"}])
@pytest.mark.parametrize("mode", ["same", "new"])
async def test_noop_is_recorded_without_a_message(backend, monkeypatch, result, mode):
    name, workflow_id = "quiet_pipeline", "quiet-123"
    backend.authoring.add_workflow(name, manifest={"schema": 1, "name": name})
    chat = backend.db.create_chat("Existing conversation", "default")
    backend.db.add_message(chat["id"], "user", "Keep this context")
    backend.db.set_workflow_settings(name, {"chat_mode": mode, "chat_id": chat["id"]})
    backend.db.record_run(name, workflow_id, "run-123", "webhook", {})
    before = backend.db.list_chats()
    messages = backend.db.list_messages(chat["id"])
    backend.temporal.executions[workflow_id] = {"status": "COMPLETED"}
    backend.temporal.results[workflow_id] = result
    monkeypatch.setattr(runs, "POLL_FLOOR_SECONDS", 0)
    monkeypatch.setattr(runs, "POLL_CEILING_SECONDS", 0)
    await runs.watch(name, workflow_id)
    assert backend.db.list_chats() == before
    assert backend.db.list_messages(chat["id"]) == messages
    assert backend.db.list_runs(name)[0]["status"] == "completed"
    assert backend.db.list_runs(name)[0]["result"] == result
    assert bus.recent(1)[0]["kind"] == "run.finished"


@pytest.mark.parametrize("result", [{"notify": False}, None, ""])
@pytest.mark.parametrize("status", ["failed", "timed_out", "canceled", "terminated"])
async def test_silence_never_hides_failures(backend, status, result):
    await runs.deliver_to_chat("pipeline", "run-1", status, result)
    chat = backend.db.list_chats()[0]
    assert len(backend.db.list_messages(chat["id"])) == 1


@pytest.mark.parametrize("result", [{"notify": "false"}, {"notify": 0}, {"notify": True}, {"count": 0}, {}])
def test_only_a_boolean_false_suppresses_structured_output(result):
    assert runs.should_deliver("completed", result)


async def test_noop_does_not_create_a_first_chat(backend):
    await runs.deliver_to_chat("pipeline", "run-1", "completed", {"notify": False})
    assert backend.db.list_chats() == []
