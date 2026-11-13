"""Cross-boundary workflow controls, transactional delivery and native context."""

import asyncio
import json
import sqlite3

import pytest
from nautionette.chat_sessions import chat_directory
from nautionette_backend import chat_sessions, conversations, run_delivery, runs
from nautionette_backend.db import Database


def workflow(backend, name="pipeline"):
    backend.authoring.add_workflow(name, manifest={"schema": 1, "name": name})
    return name


def schedule(client, name="pipeline"):
    return client.post(
        f"/api/workflows/{name}/schedule",
        json={"frequency": "daily", "at": "09:00", "timezone": "Europe/Zurich", "input": {}},
    )


def sealed(backend, result=None, status="completed", workflow_id="delivery-run"):
    result = {"summary": "A real workflow result"} if result is None else result
    backend.db.record_run("pipeline", workflow_id, "execution-id", "schedule", {})
    run_delivery.finish(workflow_id, status, result, notify=runs.should_deliver(status, result))
    return workflow_id


def native(backend):
    chat = backend.db.create_chat("Native", "default")["id"]
    directory = chat_directory(chat)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "session.jsonl"
    path.write_text(json.dumps({"type": "session", "id": "stable-session", "version": 3}) + "\n")
    backend.db.execute("UPDATE chats SET pi_session_id = 'stable-session' WHERE id = ?", (chat,))
    backend.db.set_workflow_settings("pipeline", {"chat_id": chat})
    return chat, path


def test_disable_pauses_temporal_and_reenable_preserves_recurrence(client, backend):
    workflow(backend)
    assert schedule(client).status_code == 200
    before = dict(backend.temporal.schedule_specs["pipeline"])
    assert client.patch("/api/workflows/pipeline/settings", json={"disabled": True}).status_code == 200
    assert backend.temporal.schedule_specs["pipeline"]["paused"]
    assert client.post("/api/workflows/pipeline/run", json={}).status_code == 409
    assert client.post("/api/triggers/pipeline", json={}).status_code == 409
    assert schedule(client).status_code == 409
    assert backend.temporal.cancelled == []  # Disabling never aborts work already started.
    assert client.patch("/api/workflows/pipeline/settings", json={"disabled": False}).status_code == 200
    assert backend.temporal.schedule_specs["pipeline"] == before


def test_reenable_does_not_resume_a_preexisting_operator_pause(client, backend):
    workflow(backend)
    schedule(client)
    backend.temporal.schedule_specs["pipeline"]["paused"] = True
    client.patch("/api/workflows/pipeline/settings", json={"disabled": True})
    client.patch("/api/workflows/pipeline/settings", json={"disabled": False})
    assert backend.temporal.schedule_specs["pipeline"]["paused"]


async def test_failed_disable_reports_pending_but_persists_intent_and_reconciles(
    client, backend, live, monkeypatch
):
    workflow(backend)
    schedule(client)
    original = live.temporal.set_workflow_disabled

    async def unavailable(*args):
        raise RuntimeError("private upstream failure text")

    monkeypatch.setattr(live.temporal, "set_workflow_disabled", unavailable)
    response = client.patch("/api/workflows/pipeline/settings", json={"disabled": True})
    assert response.status_code == 503
    assert response.json()["detail"]["schedule_sync_pending"] is True
    assert "private upstream" not in response.text
    assert backend.db.workflow_settings("pipeline")["disabled"]
    assert client.post("/api/workflows/pipeline/run", json={}).status_code == 409
    monkeypatch.setattr(live.temporal, "set_workflow_disabled", original)
    await runs.reconcile_schedule_timeouts()
    assert backend.temporal.schedule_specs["pipeline"]["paused"]


def test_disabling_without_a_schedule_succeeds_and_blocks_new_schedules(client, backend):
    workflow(backend)
    assert client.patch("/api/workflows/pipeline/settings", json={"disabled": True}).status_code == 200
    assert schedule(client).status_code == 409


async def test_sealing_completion_and_outbox_is_atomic(backend):
    backend.db.record_run("pipeline", "atomic", "execution", "manual", {})
    backend.db.execute(
        "CREATE TEMP TRIGGER fail_outbox BEFORE INSERT ON run_deliveries "
        "BEGIN SELECT RAISE(ABORT, 'outbox unavailable'); END"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError):
            run_delivery.finish("atomic", "completed", {"summary": "Done"}, notify=True)
        assert runs.stored_run("atomic")["status"] == "running"
        assert run_delivery.pending() == []
    finally:
        backend.db.execute("DROP TRIGGER fail_outbox")


@pytest.mark.parametrize("mode", ["same", "new"])
async def test_delivery_rolls_back_every_side_effect_then_recovers_once(backend, mode):
    backend.db.set_workflow_settings("pipeline", {"chat_mode": mode})
    sealed(backend)
    backend.db.execute(
        "CREATE TEMP TRIGGER fail_inbox BEFORE INSERT ON chat_context_messages "
        "BEGIN SELECT RAISE(ABORT, 'inbox unavailable'); END"
    )
    try:
        await runs.retry_deliveries()
        assert backend.db.list_chats() == []
        assert backend.db.query("SELECT * FROM messages") == []
        assert backend.db.workflow_settings("pipeline")["chat_id"] is None
        state = backend.db.one("SELECT * FROM run_deliveries")
        assert state["state"] == "pending" and state["attempts"] == 1
    finally:
        backend.db.execute("DROP TRIGGER fail_inbox")
    backend.db.execute("UPDATE run_deliveries SET retry_at = 0")
    await asyncio.gather(runs.retry_deliveries(), runs.retry_deliveries())
    await runs.deliver_to_chat("pipeline", "delivery-run", "completed", {"summary": "A real workflow result"})
    chats = backend.db.list_chats()
    assert len(chats) == 1
    assert len(backend.db.list_messages(chats[0]["id"])) == 1
    assert backend.db.one("SELECT state FROM run_deliveries")["state"] == "delivered"
    assert len(backend.db.query("SELECT * FROM chat_context_messages")) == 1


async def test_pending_delivery_survives_database_reopen_and_needs_no_temporal(backend, live, monkeypatch):
    sealed(backend)
    reopened = Database(backend.db.path)
    try:
        assert reopened.one("SELECT state FROM run_deliveries")["state"] == "pending"
    finally:
        reopened._conn.close()

    async def unavailable(*args, **kwargs):
        raise AssertionError("must not query Temporal to deliver a saved result")

    monkeypatch.setattr(live.temporal, "describe", unavailable)
    monkeypatch.setattr(live.temporal, "result", unavailable)
    await runs.retry_deliveries()
    assert backend.db.one("SELECT state FROM run_deliveries")["state"] == "delivered"


async def test_delivered_tombstone_prevents_resurrecting_a_deleted_chat(backend):
    sealed(backend)
    await runs.retry_deliveries()
    chat = backend.db.list_chats()[0]["id"]
    backend.db.delete_chat(chat)
    assert backend.db.query("SELECT * FROM chat_context_messages") == []
    await runs.deliver_to_chat("pipeline", "delivery-run", "completed", {"summary": "Duplicate"})
    await runs.retry_deliveries()
    assert backend.db.list_chats() == []


async def test_silent_success_is_sealed_without_chat_or_context(backend):
    sealed(backend, {"notify": False, "reason": "Nothing changed"})
    await runs.retry_deliveries()
    assert backend.db.one("SELECT state FROM run_deliveries")["state"] == "skipped"
    assert backend.db.list_chats() == []
    assert backend.db.query("SELECT * FROM chat_context_messages") == []


async def test_duplicate_completion_does_not_change_the_sealed_result(backend):
    sealed(backend)
    run_delivery.finish("delivery-run", "completed", {"notify": False}, notify=False)
    assert runs.stored_run("delivery-run")["result"] == {"summary": "A real workflow result"}
    assert backend.db.one("SELECT state FROM run_deliveries")["state"] == "pending"


async def test_transient_result_fetch_failure_is_not_sealed_as_silent_success(backend, live, monkeypatch):
    backend.db.record_run("pipeline", "fetch", "execution", "manual", {})
    backend.temporal.executions["fetch"] = {"status": "COMPLETED"}
    calls = 0

    async def result(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            assert runs.stored_run("fetch")["status"] == "running"
            raise TimeoutError("temporarily unavailable")
        return {"summary": "Recovered result"}

    monkeypatch.setattr(live.temporal, "result", result)
    monkeypatch.setattr(runs, "POLL_FLOOR_SECONDS", 0)
    monkeypatch.setattr(runs, "POLL_CEILING_SECONDS", 0)
    await runs.watch("pipeline", "fetch")
    assert calls == 2
    assert runs.stored_run("fetch")["result"] == {"summary": "Recovered result"}
    assert backend.db.one("SELECT state FROM run_deliveries")["state"] == "delivered"


async def test_workflow_result_is_passed_to_native_resume_and_acknowledged(backend, monkeypatch):
    chat, path = native(backend)
    before = path.read_bytes()
    sealed(backend)
    await runs.retry_deliveries()
    pending = chat_sessions.pending_context(chat)
    assert len(pending) == 1
    assert path.read_bytes() == before  # Delivery never writes a running/native session.
    backend.db.accept_chat_message(chat, "Explain the result above", "followup")

    async def stream(job):
        assert job["history"] == []
        assert job["external_messages"] == pending
        assert pending[0]["origin"]["trigger"] == "schedule"
        yield {"type": "session_ready", "id": "stable-session", "context_message_ids": [pending[0]["id"]]}
        yield {"type": "result", "ok": True, "text": "Explained"}

    monkeypatch.setattr(conversations, "stream_agent", stream)
    await conversations.run_turn("followup", chat, {"project_ids": []})
    assert chat_sessions.pending_context(chat) == []


async def test_fresh_session_import_does_not_duplicate_external_result_as_legacy(backend):
    sealed(backend)
    await runs.retry_deliveries()
    chat = backend.db.list_chats()[0]["id"]
    assert chat_sessions.legacy_history(chat, "next") == []
    assert len(chat_sessions.pending_context(chat)) == 1


async def test_missing_ack_remains_pending_and_ack_is_scoped_to_sent_ids(backend):
    chat, _ = native(backend)
    sealed(backend)
    await runs.retry_deliveries()
    first = chat_sessions.pending_context(chat)
    sealed(backend, workflow_id="new-result")
    await runs.retry_deliveries()
    second = chat_sessions.pending_context(chat)
    chat_sessions.acknowledge_context(chat, first, [item["id"] for item in second])
    assert [item["id"] for item in chat_sessions.pending_context(chat)] == [second[1]["id"]]
    chat_sessions.acknowledge_context("other-chat", second, [second[1]["id"]])
    assert chat_sessions.has_pending_context(chat)


async def test_results_arriving_during_a_turn_block_text_only_steering(backend, live, monkeypatch):
    chat, _ = native(backend)
    backend.db.accept_chat_message(chat, "Work", "active")
    backend.db.accept_chat_message(chat, "Explain latest", "queued", queue=True)
    backend.db.execute(
        "UPDATE chat_turns SET job = ? WHERE id = 'queued'", (json.dumps({"prompt": "Explain latest"}),)
    )
    sealed(backend)
    await runs.retry_deliveries()
    sent = []

    async def control(*args):
        sent.append(args)
        return True

    monkeypatch.setattr(live.broker, "control_agent", control)
    finished = asyncio.Event()
    task = asyncio.create_task(conversations.control_turn("active", chat, {}, finished))
    await asyncio.sleep(0)
    finished.set()
    await task
    assert sent == []
    assert backend.db.one("SELECT state FROM chat_turns WHERE id = 'queued'")["state"] == "queued"


async def test_pending_context_copies_to_a_branch_without_changing_source_identity(client, backend):
    sealed(backend)
    await runs.retry_deliveries()
    chat = backend.db.list_chats()[0]["id"]
    source = chat_sessions.pending_context(chat)[0]
    response = client.post(f"/api/chats/{chat}/branch", json={})
    assert response.status_code == 200
    branch_id = response.json()["id"]
    copied = chat_sessions.pending_context(branch_id)[0]
    assert copied["id"] != source["id"]
    assert copied["source_id"] == source["source_id"]
    assert chat_sessions.legacy_history(branch_id, "next") == []


async def test_schedule_creation_and_disable_are_serialized(backend, live, monkeypatch):
    from nautionette_backend.routers import workflows
    from nautionette_backend.schedules import DailySchedule

    workflow(backend)
    entered, release = asyncio.Event(), asyncio.Event()
    original = live.temporal.set_schedule

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(live.temporal, "set_schedule", delayed)
    creating = asyncio.create_task(
        workflows.schedule_workflow("pipeline", DailySchedule(frequency="daily", at="09:00", timezone="UTC"))
    )
    await entered.wait()
    disabling = asyncio.create_task(workflows.patch_workflow_settings("pipeline", {"disabled": True}))
    await asyncio.sleep(0)
    assert not disabling.done()
    release.set()
    await asyncio.gather(creating, disabling)
    assert backend.db.workflow_settings("pipeline")["disabled"]
    assert backend.temporal.schedule_specs["pipeline"]["paused"]


async def test_reconciliation_pauses_even_when_definition_lookup_is_broken(backend, live, monkeypatch):
    workflow(backend)
    from nautionette_backend.schedules import DailySchedule, temporal_spec

    await live.temporal.set_schedule(
        "pipeline", temporal_spec(DailySchedule(frequency="daily", at="09:00", timezone="UTC")), {}
    )
    backend.db.set_workflow_settings("pipeline", {"disabled": True})

    async def broken(*args):
        raise RuntimeError("Definition temporarily unavailable")

    monkeypatch.setattr(live.authoring, "get_workflow", broken)
    await runs.reconcile_schedule_timeouts()
    assert backend.temporal.schedule_specs["pipeline"]["paused"]


async def test_context_batch_is_bounded_and_does_not_truncate_original_evidence(backend):
    chat, _ = native(backend)
    content = "X" * 200_000
    for i in range(3):
        sealed(backend, {"summary": content}, workflow_id=f"large-{i}")
    await runs.retry_deliveries()
    pending = chat_sessions.pending_context(chat)
    assert len(pending) == 1
    assert content in pending[0]["content"]
    chat_sessions.acknowledge_context(chat, pending, [pending[0]["id"]])
    assert len(chat_sessions.pending_context(chat)) == 1
    assert len(backend.db.list_messages(chat)) == 3


async def test_termination_seals_delivery_even_when_chat_commit_fails(client, backend, monkeypatch):
    backend.db.record_run("pipeline", "terminated-run", "execution", "manual", {})
    original = run_delivery.commit

    def fail(*args, **kwargs):
        raise OSError("temporary storage failure")

    monkeypatch.setattr(run_delivery, "commit", fail)
    assert (
        client.post("/api/runs/terminated-run/terminate", json={"reason": "Stopped deliberately"}).status_code
        == 200
    )
    assert runs.stored_run("terminated-run")["status"] == "terminated"
    assert backend.db.one("SELECT state FROM run_deliveries")["state"] == "pending"
    monkeypatch.setattr(run_delivery, "commit", original)
    backend.db.execute("UPDATE run_deliveries SET retry_at = 0")
    await runs.retry_deliveries()
    chat = backend.db.list_chats()[0]["id"]
    assert "Stopped deliberately" in backend.db.list_messages(chat)[0]["content"]
