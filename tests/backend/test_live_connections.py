"""SSE recovery probes and watchdog heartbeats share the streams' auth boundary."""

from __future__ import annotations

import pytest
from nautionette_backend import conversations
from nautionette_backend.events import SSE_HEARTBEAT, EventBus


@pytest.mark.parametrize("path", ["/api/events", "/api/chats/missing/stream"])
def test_probes_require_the_same_credentials_as_streams(anonymous, path):
    assert anonymous.head(path).status_code == 401
    assert anonymous.head(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_probes_are_empty_uncached_and_do_not_create_subscribers(client, monkeypatch):
    chat = client.post("/api/chats", json={"title": "Live"}).json()
    monkeypatch.setattr(
        conversations.db, "chat_snapshot", lambda _: pytest.fail("HEAD must not serialize history")
    )
    for path in ["/api/events", f"/api/chats/{chat['id']}/stream"]:
        response = client.head(path)
        assert response.status_code == 204
        assert response.content == b""
        assert response.headers["cache-control"] == "no-cache"
    assert client.head("/api/chats/missing/stream").status_code == 404


async def test_global_idle_heartbeat_is_observable_and_not_a_stored_event(monkeypatch):
    async def timeout(awaitable, timeout):
        assert timeout == 20
        awaitable.close()
        raise TimeoutError

    bus = EventBus()
    stream = bus.stream()
    await anext(stream)
    monkeypatch.setattr("nautionette_backend.events.asyncio.wait_for", timeout)
    assert await anext(stream) == SSE_HEARTBEAT
    assert bus.history() == []
    await stream.aclose()
    assert not bus._subscribers


async def test_unchanged_chat_sends_a_named_heartbeat_every_twenty_seconds(monkeypatch):
    ticks = []

    async def tick(seconds):
        ticks.append(seconds)

    monkeypatch.setattr(conversations.asyncio, "sleep", tick)
    monkeypatch.setattr(conversations.db, "chat_snapshot", lambda _: {"chat": {"id": "chat"}})
    stream = conversations.chat_snapshots("chat")
    assert '"type": "snapshot"' in await anext(stream)
    assert await anext(stream) == SSE_HEARTBEAT
    assert ticks == [0.25] * 80
    await stream.aclose()
