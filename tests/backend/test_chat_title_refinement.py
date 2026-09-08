"""Context-based refinement, explicit repairs, and title ownership/race guarantees."""

import asyncio
import json
import sqlite3

import httpx
import pytest
from nautionette_backend import chat_titles, main
from nautionette_backend.db import Database

from ..conftest import APP_TOKEN


def conversation(db, chat_id, replies=3):
    for index in range(replies):
        db.add_message(chat_id, "user", "Fix it" if index == 0 else "I mean the chat title, not login")
        db.add_message(chat_id, "assistant", f"Understood, reply {index}")


async def test_refine_once_after_three_successful_replies(backend):
    chat = backend.db.create_chat("New chat", "default")
    conversation(backend.db, chat["id"], replies=2)
    await chat_titles.refine_chat_title(chat["id"], "model")
    assert not backend.gateway.title_requests
    backend.db.add_message(chat["id"], "assistant", "Failed", {"error": "failure"})
    backend.db.add_message(chat["id"], "assistant", "Stopped", {"interrupted": True})
    backend.db.add_message(chat["id"], "assistant", "Workflow result", {"run": {"workflow": "test"}})
    await chat_titles.refine_chat_title(chat["id"], "model")
    assert not backend.gateway.title_requests
    conversation(backend.db, chat["id"], replies=1)
    await chat_titles.refine_chat_title(chat["id"], "model")
    updated = backend.db.get_chat(chat["id"])
    assert updated["title_state"] == "refined"
    assert updated["title"] == backend.gateway.generated_title
    context = json.loads(backend.gateway.title_requests[0][2])
    assert any("not login" in message["content"] for message in context)
    assert {message["role"] for message in context} == {"user", "assistant"}
    conversation(backend.db, chat["id"])
    await chat_titles.refine_chat_title(chat["id"], "model")
    assert len(backend.gateway.title_requests) == 1


@pytest.mark.parametrize("title", ["My title", "New chat"])
async def test_manual_rename_permanently_prevents_automatic_changes(backend, title):
    chat = backend.db.create_chat("New chat", "default")
    backend.db.update_chat(chat["id"], {"title": title})
    conversation(backend.db, chat["id"])
    await chat_titles.refine_chat_title(chat["id"], "model")
    await chat_titles.rewrite_chat_title(chat["id"], "ask", "model", title)
    assert backend.db.get_chat(chat["id"])["title"] == title
    assert not backend.gateway.title_requests


async def test_failed_refinement_keeps_title_and_can_retry(backend, live, monkeypatch):
    chat = backend.db.create_chat("New chat", "default")
    conversation(backend.db, chat["id"])
    with monkeypatch.context() as patch:

        async def fail(*args):
            raise TimeoutError()

        patch.setattr(live.gateway, "chat_title", fail)
        await chat_titles.refine_chat_title(chat["id"], "model")
    assert backend.db.get_chat(chat["id"])["title_state"] == "provisional"
    assert backend.db.get_chat(chat["id"])["title"] == chat["title"]
    await chat_titles.refine_chat_title(chat["id"], "model")
    assert backend.db.get_chat(chat["id"])["title_state"] == "refined"


async def test_refinement_beats_slow_initial_title(backend, live, monkeypatch):
    chat = backend.db.create_chat("New chat", "default")
    started, release = asyncio.Event(), asyncio.Event()

    async def generate(model, prompt, text):
        if text == "opening":
            started.set()
            await release.wait()
            return "Wrong early interpretation"
        return "Repair chat titles"

    monkeypatch.setattr(live.gateway, "chat_title", generate)
    task = asyncio.create_task(chat_titles.rewrite_chat_title(chat["id"], "opening", "model", chat["title"]))
    try:
        await asyncio.wait_for(started.wait(), 2)
        conversation(backend.db, chat["id"])
        await chat_titles.refine_chat_title(chat["id"], "model")
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert backend.db.get_chat(chat["id"])["title"] == "Repair chat titles"


@pytest.mark.parametrize("original", ["New chat", "My manually set title", "I need more context to help"])
def test_explicit_regeneration_repairs_existing_titles(client, backend, original):
    chat = backend.db.create_chat(original, "default", "selected/model")
    conversation(backend.db, chat["id"])
    result = client.post(f"/api/chats/{chat['id']}/title/regenerate")
    assert result.status_code == 200
    assert result.json()["title"] == backend.gateway.generated_title
    assert result.json()["title_state"] == "refined"
    model, prompt, text = backend.gateway.title_requests[0]
    assert model == "selected/model"
    assert "Never ask for clarification" in prompt
    assert "not login" in text
    assert "Understood" in text


@pytest.mark.parametrize("failure", ["empty", "timeout", "provider"])
def test_explicit_failure_is_reported_without_changing_title(client, backend, live, monkeypatch, failure):
    chat = backend.db.create_chat("My title", "default")
    conversation(backend.db, chat["id"])

    async def generate(*args):
        if failure == "empty":
            return ""
        if failure == "timeout":
            raise TimeoutError()
        raise RuntimeError("sensitive provider response")

    monkeypatch.setattr(live.gateway, "chat_title", generate)
    result = client.post(f"/api/chats/{chat['id']}/title/regenerate")
    assert result.status_code == 502
    assert "sensitive" not in result.text
    updated = backend.db.get_chat(chat["id"])
    assert updated["title"] == chat["title"]
    assert updated["title_state"] == "manual"


@pytest.mark.parametrize("change", ["rename", "same-title", "delete"])
def test_explicit_regeneration_respects_newer_changes(client, backend, live, monkeypatch, change):
    chat = backend.db.create_chat("My title", "default")
    conversation(backend.db, chat["id"])

    async def generate(*args):
        if change == "delete":
            backend.db.delete_chat(chat["id"])
        else:
            backend.db.update_chat(chat["id"], {"title": "New name" if change == "rename" else chat["title"]})
        return "Generated title"

    monkeypatch.setattr(live.gateway, "chat_title", generate)
    result = client.post(f"/api/chats/{chat['id']}/title/regenerate")
    assert result.status_code == 409
    updated = backend.db.get_chat(chat["id"])
    expected = {"rename": "New name", "same-title": chat["title"], "delete": None}
    assert (updated["title"] if updated else None) == expected[change]


def test_regeneration_requires_auth_and_conversation(client, anonymous, backend):
    chat = backend.db.create_chat("New chat", "default")
    path = f"/api/chats/{chat['id']}/title/regenerate"
    assert anonymous.post(path).status_code == 401
    assert client.post(path).status_code == 400
    assert client.post("/api/chats/missing/title/regenerate").status_code == 404
    assert not backend.gateway.title_requests


def test_custom_creation_and_rename_record_ownership(client):
    chat = client.post("/api/chats", json={"title": "New chat"}).json()
    assert chat["title_state"] == "manual"
    fresh = client.post("/api/chats", json={}).json()
    assert fresh["title_state"] == "provisional"
    result = client.patch(f"/api/chats/{fresh['id']}", json={"title": "New chat"})
    assert result.json()["title_state"] == "manual"
    assert result.json()["title_revision"] > fresh["title_revision"]


def test_context_keeps_opening_and_recent_clarifications_but_not_tool_data(backend):
    chat = backend.db.create_chat("New chat", "default")
    backend.db.add_message(chat["id"], "user", "Opening topic " + "x" * 20000)
    for index in range(20):
        backend.db.add_message(chat["id"], "assistant", f"Reply {index} " + "x" * 20000)
    backend.db.add_message(chat["id"], "tool", "TOOL OUTPUT")
    backend.db.add_message(chat["id"], "user", "QUEUED INPUT", {"queued": True})
    backend.db.add_message(chat["id"], "user", "Actually fix chat titles")
    context = chat_titles.title_context(chat_titles.title_messages(chat["id"]))
    assert len(context) < 12000
    assert "Opening topic" in context
    assert "Actually fix chat titles" in context
    assert "Reply 19" in context
    assert "Reply 2 " not in context
    assert "TOOL OUTPUT" not in context
    assert "QUEUED INPUT" not in context


def test_migration_preserves_legacy_titles_and_is_idempotent(tmp_path):
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE chats (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
            "agent_set TEXT, created_at REAL, updated_at REAL, promoted_to TEXT)"
        )
        connection.execute("INSERT INTO chats VALUES ('legacy', 'My old title', 'default', 1, 1, NULL)")
    for _ in range(2):
        database = Database(path)
        try:
            chat = database.get_chat("legacy")
            assert chat["title"] == "My old title"
            assert chat["title_state"] == "manual"
            assert chat["title_revision"] == 0
        finally:
            database._conn.close()


async def test_real_turn_completion_schedules_only_one_refinement(backend):
    chat = backend.db.create_chat("New chat", "default")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://backend.test",
        headers={"Authorization": f"Bearer {APP_TOKEN}"},
    ) as client:
        for index in range(4):
            result = await client.post(
                f"/api/chats/{chat['id']}/messages",
                json={"text": f"Clarification {index}"},
            )
            assert result.status_code == 200
            assert '"type": "done"' in result.text
            await asyncio.sleep(0)
            assert len(backend.gateway.title_requests) == (1 if index < 2 else 2)
    assert backend.db.get_chat(chat["id"])["title_state"] == "refined"
    assert "Clarification 2" in backend.gateway.title_requests[-1][2]
