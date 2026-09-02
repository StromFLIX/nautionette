"""Chats, and the stream a chat answers with."""

from __future__ import annotations

import json


def sse_events(response) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]


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
    assert client.get(f"/api/chats/{chat['id']}").json()["chat"]["title"] == (
        "Summarise the release notes"
    )


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


# --------------------------------------------------------------------- promote


def test_promoting_a_chat_writes_a_draft(client, broker, authoring):
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

    draft = client.post(f"/api/chats/{chat['id']}/promote").json()
    assert draft["origin"] == "agent"
    assert draft["name"] == "release_digest"
    assert "release_digest" in authoring.drafts
    assert client.get(f"/api/chats/{chat['id']}").json()["chat"]["promoted_to"] == "release_digest"


def test_promotion_falls_back_to_a_file_a_human_can_finish(client, broker, authoring):
    chat = client.post("/api/chats", json={"title": "Daily digest"}).json()
    send(client, chat["id"], "every morning, summarise the news")
    draft = client.post(f"/api/chats/{chat['id']}/promote").json()
    assert draft["origin"] == "scaffold"
    assert draft["name"] == "daily_digest"
    assert "@workflow.defn" in authoring.drafts["daily_digest"]["code"]


def test_an_empty_chat_has_nothing_to_promote(client):
    chat = client.post("/api/chats", json={}).json()
    assert client.post(f"/api/chats/{chat['id']}/promote").status_code == 400
    assert client.post("/api/chats/nope/promote").status_code == 404
