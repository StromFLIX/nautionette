import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from nautionette_backend import conversations, project_credentials, projects
from nautionette_backend.clients.docker_broker import BrokerClient
from nautionette_backend.project_credentials import TurnCredentials

from ..conftest import INTERNAL_TOKEN


def batch(token, expires, name="owner/repo"):
    return [
        {"full_name": name, "token": token, "expires_at": datetime.fromtimestamp(expires, UTC).isoformat()}
    ]


@pytest.fixture
def access(backend, monkeypatch):
    clock = SimpleNamespace(now=10000)
    monkeypatch.setattr(project_credentials, "time", SimpleNamespace(time=lambda: clock.now))
    chat = backend.db.create_chat("Long running", "default")["id"]
    backend.db.accept_chat_message(chat, "Work", "turn")
    mint = AsyncMock(side_effect=lambda ids: batch(f"token-{mint.await_count}", clock.now + 3600))
    revoke = AsyncMock()
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr(projects, "agent_credentials", mint)
    monkeypatch.setattr(projects, "revoke_credentials", revoke)
    monkeypatch.setattr(project_credentials.broker, "refresh_project_credentials", deliver)
    selected = ["selected"]
    credentials = TurnCredentials(chat, "turn", selected)
    return SimpleNamespace(
        clock=clock,
        chat=chat,
        mint=mint,
        revoke=revoke,
        deliver=deliver,
        credentials=credentials,
        selected=selected,
        db=backend.db,
    )


async def test_rotation_uses_frozen_selection_and_retains_only_unexpired_tokens(access):
    a = access
    initial = await a.credentials.start()
    a.selected.append("not-selected")
    for _ in range(3):
        a.clock.now += 3300
        assert await a.credentials.refresh()
        a.mint.assert_awaited_with(["selected"])
        a.deliver.assert_awaited_with(a.chat, "turn", a.credentials.current)
        assert len(a.credentials.issued) == 2
        a.revoke.assert_not_awaited()  # Do not break a Git request using the previous batch.
    assert initial != a.credentials.current
    await a.credentials.revoke()
    assert [item["token"] for item in a.revoke.call_args.args[0]] == ["token-3", "token-4"]
    assert not a.credentials.issued


async def test_delivery_timeout_retries_same_batch_and_revokes_even_unacknowledged_tokens(access):
    a = access
    await a.credentials.start()
    a.clock.now += 3300
    a.deliver.side_effect = [TimeoutError("delivery may have succeeded"), False, True]
    for _ in range(2):
        with pytest.raises((TimeoutError, RuntimeError)):
            await a.credentials.refresh()
    assert a.mint.await_count == 2
    pending = a.credentials.pending
    assert await a.credentials.refresh()
    assert a.mint.await_count == 2
    assert a.credentials.current == pending
    a.clock.now += 3300
    a.deliver.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        await a.credentials.refresh()
    await a.credentials.revoke()
    assert {item["token"] for item in a.revoke.call_args.args[0]} == {"token-2", "token-3"}


async def test_expiring_undelivered_batch_is_replaced_after_outage(access):
    a = access
    await a.credentials.start()
    a.deliver.return_value = False
    for _ in range(2):
        a.clock.now += 3600
        with pytest.raises(RuntimeError):
            await a.credentials.refresh()
    assert a.mint.await_count == 3
    a.deliver.return_value = True
    assert await a.credentials.refresh()
    assert len(a.credentials.issued) == 1


@pytest.mark.parametrize("change", ["stop", "finished", "deleted", "wrong-chat"])
async def test_inactive_turns_never_mint_or_deliver(access, change):
    a = access
    await a.credentials.start()
    if change == "stop":
        a.db.execute("UPDATE chat_turns SET stop_requested = 1 WHERE id = 'turn'")
    elif change == "finished":
        a.db.finish_chat_turn("turn", "done", {})
    elif change == "deleted":
        a.db.execute("DELETE FROM chats WHERE id = ?", (a.chat,))
    else:
        a.credentials.chat_id = "another-chat"
    assert not await a.credentials.refresh()
    assert a.mint.await_count == 1
    a.deliver.assert_not_awaited()


async def test_stop_during_issuance_revokes_but_does_not_deliver(access):
    a = access
    await a.credentials.start()

    async def mint(ids):
        a.db.execute("UPDATE chat_turns SET stop_requested = 1 WHERE id = 'turn'")
        return batch("new-token", a.clock.now + 3600)

    a.mint.side_effect = mint
    assert not await a.credentials.refresh()
    a.deliver.assert_not_awaited()
    await a.credentials.revoke()
    assert len(a.revoke.call_args.args[0]) == 2


async def test_changed_repository_scope_fails_closed(access):
    a = access
    await a.credentials.start()
    a.mint.side_effect = None
    a.mint.return_value = batch("wrong-token", a.clock.now + 3600, "owner/other")
    with pytest.raises(RuntimeError, match="scope changed"):
        await a.credentials.refresh()
    a.deliver.assert_not_awaited()
    await a.credentials.revoke()
    assert len(a.revoke.call_args.args[0]) == 2


async def test_maintenance_renews_five_minutes_early_and_retries_without_secret_logs(
    access, monkeypatch, caplog
):
    a = access
    await a.credentials.start()
    finished = asyncio.Event()
    started = asyncio.Event()
    a.clock.now += 3301
    monkeypatch.setattr(project_credentials, "RETRY_SECONDS", 0)
    a.mint.side_effect = [RuntimeError("sensitive-upstream-response"), batch("new-token", a.clock.now + 3600)]

    async def deliver(*args):
        started.set()
        return True

    a.deliver.side_effect = deliver
    task = asyncio.create_task(a.credentials.maintain(finished))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert a.mint.await_count == 3
        assert a.credentials.current[0]["token"] == "new-token"
        assert "renewal failed; retrying" in caplog.text
        assert "sensitive-upstream-response" not in caplog.text
    finally:
        finished.set()
        await asyncio.wait_for(task, 1)
    assert a.deliver.await_count == 1


async def test_finishing_wakes_maintenance_without_waiting_for_next_renewal(access):
    a = access
    await a.credentials.start()
    finished = asyncio.Event()
    task = asyncio.create_task(a.credentials.maintain(finished))
    await asyncio.sleep(0)
    finished.set()
    await asyncio.wait_for(task, 1)
    a.deliver.assert_not_awaited()
    assert a.mint.await_count == 1


@pytest.mark.parametrize("outcome", ["success", "failure", "shutdown", "stop", "cleanup-failed"])
async def test_run_turn_stops_renewal_before_cleanup_and_revokes_all_batches(
    access, backend, monkeypatch, outcome
):
    a = access
    monkeypatch.setattr(projects, "prepare_worktrees", Mock(return_value={}))
    # The initial batch is already due (e.g. a slow image build).
    a.mint.side_effect = [batch("initial", a.clock.now + 100), batch("renewed", a.clock.now + 3600)]
    delivering, cancelled, streaming = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def deliver(*args):
        delivering.set()
        try:
            await asyncio.Event().wait()  # Simulate an ambiguous in-flight broker response.
        finally:
            cancelled.set()

    async def agent(job):
        assert job["project_credentials"][0]["token"] == "initial"
        streaming.set()
        await delivering.wait()
        if outcome == "shutdown":
            await asyncio.Event().wait()
        if outcome == "stop":
            a.db.execute("UPDATE chat_turns SET stop_requested = 1 WHERE id = 'turn'")
        if outcome == "failure":
            raise RuntimeError("agent failed")
        yield {"type": "result", "ok": True, "text": "Finished"}

    async def cleanup(chat, turn):
        assert cancelled.is_set()
        assert (chat, turn) == (a.chat, "turn")
        a.revoke.assert_not_awaited()
        if outcome == "cleanup-failed":
            raise RuntimeError("Docker unavailable")

    a.deliver.side_effect = deliver
    monkeypatch.setattr(conversations, "stream_agent", agent)
    monkeypatch.setattr(conversations.broker, "cleanup_chat_agent", cleanup)
    monkeypatch.setattr(conversations, "launch_next", Mock())
    task = asyncio.create_task(conversations.run_turn("turn", a.chat, {"project_ids": ["selected"]}))
    await asyncio.wait_for(streaming.wait(), 1)
    if outcome == "shutdown":
        await asyncio.wait_for(delivering.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await asyncio.wait_for(task, 2)
    assert a.mint.await_count == 2
    assert {item["token"] for item in a.revoke.call_args.args[0]} == {"initial", "renewed"}
    assert backend.db.chat_snapshot(a.chat)["active_turn"] is None


async def test_refresh_client_authenticates_exact_turn_and_requires_acknowledgement(http):
    payload = batch("token", 20000)

    def deliver(request):

        assert request.headers["X-Internal-Token"] == INTERNAL_TOKEN
        assert json.loads(request.content) == {"chat_id": "chat", "turn_id": "turn", "credentials": payload}
        return httpx.Response(200, json={"ok": True})

    http["http://broker.test/agent/project-credentials"] = deliver
    assert await BrokerClient("http://broker.test").refresh_project_credentials("chat", "turn", payload)
