"""Automatic titles describe the ask without holding up or taking over the chat."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from nautionette_backend import chat_titles, main
from nautionette_backend.events import bus
from nautionette_backend.routers import chats

from ..conftest import APP_TOKEN


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('Title: "Fix chat titles"', "Fix chat titles"),
        ("  **Summarise release notes.**  ", "Summarise release notes"),
        ("Explain\n database\tindexing", "Explain database indexing"),
        ("One two three four five six seven eight", "One two three four five six"),
        (
            "Investigate authentication configuration regression today",
            "Investigate authentication configuration",
        ),
        ("ログインの問題を修正", "ログインの問題を修正"),
        ("x" * 80, ""),
        (" \n ", ""),
        (None, ""),
    ],
)
def test_generated_titles_are_short_and_plain(text, expected):
    assert chat_titles.clean_title(text) == expected


async def test_title_changes_are_persisted_and_broadcast(backend, monkeypatch):
    chat = backend.db.create_chat("Opening message", "default")
    monkeypatch.setattr(chat_titles.time, "time", lambda: chat["updated_at"] + 1)
    await chat_titles.rewrite_chat_title(chat["id"], "Please summarise releases", "model", chat["title"])
    updated = backend.db.get_chat(chat["id"])
    assert updated["title"] == "Summarise release notes"
    assert updated["updated_at"] > chat["updated_at"]
    assert bus.history()[-1]["kind"] == "chat.updated"
    assert bus.history()[-1]["chat_id"] == chat["id"]


@pytest.mark.parametrize("action", ["rename", "delete"])
async def test_late_generation_does_not_overwrite_user_changes(backend, live, monkeypatch, action):
    chat = backend.db.create_chat("Opening message", "default")

    async def generate(*args):
        if action == "rename":
            backend.db.update_chat(chat["id"], {"title": "My own title"})
        else:
            backend.db.delete_chat(chat["id"])
        return "Generated task title"

    monkeypatch.setattr(live.gateway, "chat_title", generate)
    await chat_titles.rewrite_chat_title(chat["id"], "ask", "model", chat["title"])
    updated = backend.db.get_chat(chat["id"])
    assert (updated["title"] if updated else None) == ("My own title" if action == "rename" else None)


@pytest.mark.parametrize("failure", ["error", "empty", "timeout"])
async def test_title_failures_keep_the_preview(backend, live, monkeypatch, failure):
    chat = backend.db.create_chat("Opening message", "default")

    async def generate(*args):
        if failure == "error":
            raise httpx.ConnectError("unavailable")
        if failure == "timeout":
            raise TimeoutError()
        return ""

    monkeypatch.setattr(live.gateway, "chat_title", generate)
    await chat_titles.rewrite_chat_title(chat["id"], "ask", "model", chat["title"])
    assert backend.db.get_chat(chat["id"])["title"] == chat["title"]


async def test_slow_title_generation_does_not_block_the_answer(backend, live, monkeypatch):
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    requests = []
    original = chat_titles.rewrite_chat_title

    async def generate(*args):
        requests.append(args)
        started.set()
        await release.wait()
        return "Fix login redirect"

    async def rewrite(*args):
        try:
            await original(*args)
        finally:
            finished.set()

    monkeypatch.setattr(live.gateway, "chat_title", generate)
    monkeypatch.setattr(chats, "rewrite_chat_title", rewrite)
    chat = backend.db.create_chat("New chat", "default", "openai/gpt-4o-mini")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app),
        base_url="http://backend.test",
        headers={"Authorization": f"Bearer {APP_TOKEN}"},
    ) as client:
        try:
            response = await asyncio.wait_for(
                client.post(
                    f"/api/chats/{chat['id']}/messages",
                    json={"text": "Please fix the login redirect", "message_id": "title-test"},
                ),
                timeout=2,
            )
            assert response.status_code == 200
            assert '"type": "done"' in response.text
            assert started.is_set()
            assert not finished.is_set()
            # Retrying the same accepted message must not schedule a second title call.
            retry = await client.post(
                f"/api/chats/{chat['id']}/messages",
                json={"text": "Please fix the login redirect", "message_id": "title-test"},
                headers={"Accept": "application/json"},
            )
            assert retry.status_code == 202
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), timeout=2)
    assert backend.db.get_chat(chat["id"])["title"] == "Fix login redirect"
    assert len(requests) == 1
