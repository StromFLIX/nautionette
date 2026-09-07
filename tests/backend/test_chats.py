"""Chats, and the stream a chat answers with."""

from __future__ import annotations

import asyncio
import json

import httpx
from nautionette_backend import background, conversations, main

from ..conftest import APP_TOKEN


def sse_events(response) -> list[dict]:
    return [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith("data:")]


def send(client, chat_id, text):
    return client.post(f"/api/chats/{chat_id}/messages", json={"text": text})


def test_a_new_chat_inherits_the_current_defaults(client):
    chat = client.post("/api/chats", json={}).json()
    assert chat["title"] == "New chat"
    assert chat["agent_set"] == "default"
    assert chat["model"] == "openai/gpt-4o-mini"
    assert chat["tools"] is None
    assert client.get("/api/chats").json()["chats"][0]["id"] == chat["id"]


def test_a_chat_can_be_pointed_somewhere_else(client):
    chat = client.post("/api/chats", json={"title": "Research", "model": "groq/llama"}).json()
    updated = client.patch(
        f"/api/chats/{chat['id']}", json={"title": "Renamed", "tools": ["linear_search"]}
    ).json()
    assert updated["title"] == "Renamed"
    assert updated["model"] == "groq/llama"
    assert updated["tools"] == ["linear_search"]


def test_clearing_the_tools_means_every_tool_again(client):
    chat = client.post("/api/chats", json={"tools": ["a", "b"]}).json()
    assert client.patch(f"/api/chats/{chat['id']}", json={"tools": None}).json()["tools"] is None


def test_patching_a_chat_that_does_not_exist_is_a_404(client):
    assert client.patch("/api/chats/nope", json={"title": "x"}).status_code == 404
    assert client.get("/api/chats/nope").status_code == 404


def test_deleting_a_chat_takes_its_messages(client, db):
    chat = client.post("/api/chats", json={}).json()
    send(client, chat["id"], "hello")
    client.delete(f"/api/chats/{chat['id']}")
    assert client.get("/api/chats").json()["chats"] == []
    assert db.query("SELECT * FROM messages") == []


def test_a_message_is_answered_and_both_halves_are_kept(client):
    chat = client.post("/api/chats", json={}).json()
    events = sse_events(send(client, chat["id"], "hello there"))
    assert [event["type"] for event in events] == ["user_message", "delta", "result", "done"]
    assert events[0]["message"]["content"] == "hello there"
    assert events[-1]["message"]["role"] == "assistant"
    assert events[-1]["message"]["content"] == "hello"

    messages = client.get(f"/api/chats/{chat['id']}").json()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "hello there"),
        ("assistant", "hello"),
    ]


def test_the_first_message_names_an_unnamed_chat(client):
    chat = client.post("/api/chats", json={}).json()
    send(client, chat["id"], "Summarise the release notes\nand nothing else")
    assert client.get(f"/api/chats/{chat['id']}").json()["chat"]["title"] == ("Summarise the release notes")


def test_a_named_chat_keeps_its_name(client):
    chat = client.post("/api/chats", json={"title": "Standing order"}).json()
    send(client, chat["id"], "hello")
    assert client.get(f"/api/chats/{chat['id']}").json()["chat"]["title"] == "Standing order"


def test_the_transcript_so_far_is_handed_to_the_agent(client, broker):
    chat = client.post("/api/chats", json={}).json()
    send(client, chat["id"], "first")
    send(client, chat["id"], "second")
    assert broker.jobs[0]["history"] == []
    assert broker.jobs[1]["history"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "hello"},
    ]


def test_the_chat_decides_the_agent_set_model_and_tools(client, broker):
    chat = client.post(
        "/api/chats", json={"agent_set": "research", "model": "groq/llama", "tools": ["search"]}
    ).json()
    send(client, chat["id"], "hello")
    job = broker.jobs[0]
    assert (job["agent_set"], job["model"], job["tools"]) == ("research", "groq/llama", ["search"])
    assert job["mode"] == "interactive"
    assert job["run_id"] == f"chat-{chat['id']}"


def test_an_empty_message_is_refused(client):
    chat = client.post("/api/chats", json={}).json()
    assert send(client, chat["id"], "   ").status_code == 400


def test_retrying_a_message_does_not_run_the_agent_twice(client, broker):
    chat = client.post("/api/chats", json={}).json()
    path = f"/api/chats/{chat['id']}/messages"
    payload = {"text": "hello", "message_id": "device-message-1"}
    first = sse_events(client.post(path, json=payload))
    second = sse_events(client.post(path, json=payload))
    assert first[0]["message"]["id"] == second[0]["message"]["id"]
    assert first[-1]["message"]["id"] == second[-1]["message"]["id"]
    assert len(broker.jobs) == 1
    assert len(client.get(f"/api/chats/{chat['id']}").json()["messages"]) == 2


def test_a_reused_message_id_cannot_change_its_text_or_chat(client):
    first = client.post("/api/chats", json={}).json()["id"]
    second = client.post("/api/chats", json={}).json()["id"]
    payload = {"text": "hello", "message_id": "stable-id"}
    client.post(f"/api/chats/{first}/messages", json=payload)
    assert client.post(f"/api/chats/{first}/messages", json={**payload, "text": "changed"}).status_code == 422
    assert client.post(f"/api/chats/{second}/messages", json=payload).status_code == 422


async def test_disconnected_senders_and_subscribers_do_not_own_generation(backend, monkeypatch):
    started = asyncio.Event()
    finish = asyncio.Event()

    async def agent(job):
        yield {"type": "delta", "text": "Already working"}
        started.set()
        await finish.wait()
        yield {"type": "delta", "text": ", finished"}
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    transport = httpx.ASGITransport(app=main.app)
    headers = {"Authorization": f"Bearer {APP_TOKEN}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as web:
            chat_id = (await web.post("/api/chats", json={})).json()["id"]
            response = await web.post(
                f"/api/chats/{chat_id}/messages", json={"text": "hello", "message_id": "web-1"}
            )
            assert response.status_code == 202
        await asyncio.wait_for(started.wait(), 2)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as android:
            snapshot = (await android.get(f"/api/chats/{chat_id}")).json()
            assert snapshot["messages"][0]["id"] == "web-1"
            assert snapshot["active_turn"]["steps"][0]["text"] == "Already working"
            assert (await android.get("/api/chats")).json()["chats"][0]["answering"] is True
            subscriber = conversations.chat_snapshots(chat_id)
            frame = json.loads((await anext(subscriber))[6:])
            assert frame["active_turn"] == snapshot["active_turn"]
            await subscriber.aclose()
            retry = await android.post(
                f"/api/chats/{chat_id}/messages", json={"text": "hello", "message_id": "web-1"}
            )
            assert retry.json()["message"]["id"] == "web-1"
            busy = await android.post(
                f"/api/chats/{chat_id}/messages", json={"text": "another", "message_id": "android-1"}
            )
            assert busy.status_code == 409
            assert len(backend.db.list_messages(chat_id)) == 1
            finish.set()
            await asyncio.wait_for(asyncio.gather(*background._running), 2)
            recovered = (await android.get(f"/api/chats/{chat_id}")).json()
            assert recovered["active_turn"] is None
            assert (await android.get("/api/chats")).json()["chats"][0]["answering"] is False
            assert recovered["messages"][-1]["content"] == "Already working, finished"
    finally:
        finish.set()
        await background.drain()


async def test_separate_conversations_run_concurrently(backend, monkeypatch):
    started = set()
    both_started = asyncio.Event()
    finish = asyncio.Event()

    async def agent(job):
        started.add(job["run_id"])
        if len(started) == 2:
            both_started.set()
        yield {"type": "delta", "text": job["prompt"]}
        await finish.wait()
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {APP_TOKEN}", "Accept": "application/json"},
        ) as client:
            chat_ids = []
            for text in ("first", "second"):
                chat_id = (await client.post("/api/chats", json={})).json()["id"]
                chat_ids.append(chat_id)
                response = await client.post(f"/api/chats/{chat_id}/messages", json={"text": text})
                assert response.status_code == 202
            await asyncio.wait_for(both_started.wait(), 2)
            for chat_id, text in zip(chat_ids, ("first", "second"), strict=True):
                snapshot = (await client.get(f"/api/chats/{chat_id}")).json()
                assert snapshot["active_turn"]["steps"][0]["text"] == text
            finish.set()
            await asyncio.wait_for(asyncio.gather(*background._running), 2)
    finally:
        finish.set()
        await background.drain()


def test_restart_preserves_partial_output_without_repeating_tools(client, db, broker):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    db.accept_chat_message(chat_id, "hello", "interrupted-1")
    db.execute("UPDATE messages SET created_at = created_at + 3600 WHERE id = ?", ("interrupted-1",))
    db.record_chat_progress(
        "interrupted-1", {"type": "delta", "text": "Partial"}, [{"kind": "text", "text": "Partial"}], ""
    )
    conversations.recover_interrupted()
    conversations.recover_interrupted()
    snapshot = client.get(f"/api/chats/{chat_id}").json()
    assert snapshot["active_turn"] is None
    assert len(snapshot["messages"]) == 2
    assert snapshot["messages"][-1]["content"] == "Partial"
    assert "restart" in snapshot["messages"][-1]["meta"]["error"]
    assert broker.jobs == []
    events = sse_events(
        client.post(f"/api/chats/{chat_id}/messages", json={"text": "hello", "message_id": "interrupted-1"})
    )
    assert events[-1]["message"]["content"] == "Partial"
    assert broker.jobs == []


def test_long_chats_recover_the_latest_messages_too(client, db):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    for index in range(501):
        db.add_message(chat_id, "assistant", f"message {index}")
    snapshot = client.get(f"/api/chats/{chat_id}").json()
    assert len(snapshot["messages"]) == 501
    assert snapshot["messages"][-1]["content"] == "message 500"


def test_a_message_to_a_chat_that_does_not_exist_is_a_404(client):
    assert send(client, "nope", "hello").status_code == 404


def test_an_agent_that_fails_still_leaves_something_in_the_chat(client, broker):
    broker.events = [{"type": "error", "message": "no model configured"}]
    chat = client.post("/api/chats", json={}).json()
    events = sse_events(send(client, chat["id"], "hello"))
    assert events[-1]["message"]["content"] == "The agent could not answer: no model configured"
    assert events[-1]["message"]["meta"]["error"] == "no model configured"


def test_the_tools_an_answer_used_are_recorded(client, broker):
    broker.events = [
        {"type": "tool", "name": "linear_search"},
        {"type": "result", "ok": True, "text": "found it"},
    ]
    chat = client.post("/api/chats", json={}).json()
    events = sse_events(send(client, chat["id"], "hello"))
    assert events[-1]["message"]["meta"]["tools"] == ["linear_search"]
    assert events[-1]["message"]["content"] == "found it"


def test_an_answer_keeps_the_order_of_what_it_said_and_did(client, broker):
    broker.events = [
        {"type": "delta", "text": "Looking"},
        {"type": "delta", "text": " it up."},
        {"type": "tool", "id": "c1", "name": "linear_search", "args": {"query": "moon"}},
        {"type": "tool_done", "id": "c1", "name": "linear_search", "result": "one hit"},
        {"type": "delta", "text": "\n\nFound one."},
        {"type": "result", "ok": True, "text": ""},
    ]
    chat = client.post("/api/chats", json={}).json()
    steps = sse_events(send(client, chat["id"], "hello"))[-1]["message"]["meta"]["steps"]
    assert [step["kind"] for step in steps] == ["text", "tool", "text"]
    assert steps[0]["text"] == "Looking it up."
    assert steps[1] == {
        "kind": "tool",
        "id": "c1",
        "name": "linear_search",
        "args": {"query": "moon"},
        "ok": True,
        "result": "one hit",
    }


def test_a_tool_that_failed_says_so(client, broker):
    broker.events = [
        {"type": "tool", "id": "c1", "name": "linear_search"},
        {"type": "tool_done", "id": "c1", "name": "linear_search", "error": True, "result": "boom"},
        {"type": "result", "ok": True, "text": "sorry"},
    ]
    chat = client.post("/api/chats", json={}).json()
    steps = sse_events(send(client, chat["id"], "hello"))[-1]["message"]["meta"]["steps"]
    assert (steps[0]["ok"], steps[0]["result"]) == (False, "boom")


def test_provider_context_is_persisted_from_the_latest_request(client, broker):
    context = {"model": "openai/gpt-4o-mini", "tokens": 4600, "source": "provider"}
    broker.events = [
        {"type": "usage", "context": {**context, "tokens": 9000}},
        {"type": "usage", "context": context},
        {"type": "result", "ok": True, "text": "hello", "context": context},
    ]
    chat_id = client.post("/api/chats", json={}).json()["id"]
    events = sse_events(send(client, chat_id, "hello"))
    assert events[-1]["message"]["meta"]["context"] == context
    assert client.get(f"/api/chats/{chat_id}").json()["messages"][-1]["meta"]["context"] == context


def test_live_context_survives_reconnect_and_backend_restart(client, db):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    db.accept_chat_message(chat_id, "hello", "usage-turn")
    assert db.chat_snapshot(chat_id)["active_turn"]["context"] is None
    context = {"model": "test/model", "tokens": 1234, "source": "provider"}
    db.record_chat_progress("usage-turn", {"type": "usage", "context": context}, [], "")
    db.record_chat_progress("usage-turn", {"type": "delta", "text": "hi"}, [], "")
    assert client.get(f"/api/chats/{chat_id}").json()["active_turn"]["context"] == context
    conversations.recover_interrupted()
    assert db.chat_snapshot(chat_id)["messages"][-1]["meta"]["context"] == context
    db.accept_chat_message(chat_id, "again", "next-turn")
    assert db.chat_snapshot(chat_id)["active_turn"]["context"] is None


def test_missing_context_clears_a_previous_measurement(client, db):
    chat_id = client.post("/api/chats", json={}).json()["id"]
    db.accept_chat_message(chat_id, "hello", "missing-usage")
    db.record_chat_progress("missing-usage", {"type": "usage", "context": {"tokens": 1234}}, [], "")
    db.record_chat_progress("missing-usage", {"type": "usage", "context": None}, [], "")
    assert db.chat_snapshot(chat_id)["active_turn"]["context"] is None
    db.finish_chat_turn("missing-usage", "hi", {})
    assert db.chat_snapshot(chat_id)["messages"][-1]["meta"]["context"] is None


# --------------------------------------------------------------------- promote


def test_promoting_a_chat_deploys_without_approval(client, broker, authoring):
    broker.events = [
        {
            "type": "result",
            "ok": True,
            "text": "",
            "output": {
                "name": "release_digest",
                "title": "Release digest",
                "code": "MANIFEST = {}\n",
            },
        }
    ]
    chat = client.post("/api/chats", json={"title": "Release digest"}).json()
    send(client, chat["id"], "summarise releases every morning")

    published = client.post(f"/api/chats/{chat['id']}/promote").json()
    assert published["origin"] == "agent"
    assert published["name"] == "release_digest"
    assert published["ready"] is True
    assert "release_digest" in authoring.workflows
    assert authoring.drafts == {}
    assert client.get(f"/api/chats/{chat['id']}").json()["chat"]["promoted_to"] == "release_digest"


def test_promotion_can_deploy_a_validated_fallback(client, broker, authoring):
    chat = client.post("/api/chats", json={"title": "Daily digest"}).json()
    send(client, chat["id"], "every morning, summarise the news")
    published = client.post(f"/api/chats/{chat['id']}/promote").json()
    assert published["origin"] == "scaffold"
    assert published["name"] == "daily_digest"
    assert "@workflow.defn" in authoring.workflows["daily_digest"]["code"]
    assert authoring.drafts == {}


def test_an_empty_chat_has_nothing_to_promote(client):
    chat = client.post("/api/chats", json={}).json()
    assert client.post(f"/api/chats/{chat['id']}/promote").status_code == 400
    assert client.post("/api/chats/nope/promote").status_code == 404
