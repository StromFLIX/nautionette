"""Agent rail activity comes from durable turns, not a truncated chat list."""

from nautionette_backend import conversations


def test_activity_counts_all_chats_and_only_current_agent_membership(client, db):
    assert client.get("/api/chats").json()["activity"] == {"running": 0, "queued": 0, "agents": {}}
    chats = [
        db.create_chat("Ada one", "default", agent_id="ada"),
        db.create_chat("Ada two", "default", agent_id="ada"),
        db.create_chat("Grace", "default", agent_id="grace"),
        db.create_chat("Custom Ada", "default", agent_id="ada", agent_customized=True),
        db.create_chat("Unassigned", "default"),
    ]
    for index, chat in enumerate(chats):
        db.accept_chat_message(chat["id"], "Run", f"run-{index}")
        db.accept_chat_message(chat["id"], "Wait", f"wait-{index}", queue=True)
    # Counts must include active chats outside the 200-row recent list.
    for index in range(201):
        db.create_chat(f"Newer idle chat {index}", "default")
    response = client.get("/api/chats").json()
    assert len(response["chats"]) == 200
    assert not {chat["id"] for chat in chats} & {chat["id"] for chat in response["chats"]}
    assert response["activity"] == {
        "running": 5,
        "queued": 5,
        "agents": {"ada": {"running": 2, "queued": 2}, "grace": {"running": 1, "queued": 1}},
    }
    db.update_chat(chats[0]["id"], {"agent_id": "grace"})
    db.update_chat(chats[1]["id"], {"agent_customized": True})
    activity = client.get("/api/chats").json()["activity"]
    assert activity == {"running": 5, "queued": 5, "agents": {"grace": {"running": 2, "queued": 2}}}


def test_activity_tracks_steering_pause_resume_discard_and_completion(client, db, monkeypatch):
    events = []
    monkeypatch.setattr(conversations.bus, "publish", lambda kind, data: events.append((kind, data)))
    spawned = []

    def spawn(coroutine, **kwargs):
        spawned.append(kwargs)
        coroutine.close()

    monkeypatch.setattr(conversations, "spawn", spawn)
    chat_id = db.create_chat("Ada", "default", agent_id="ada")["id"]
    db.accept_chat_message(chat_id, "Run", "active")
    for message_id in ("steer", "next", "discard"):
        db.accept_chat_message(chat_id, message_id, message_id, queue=True)
        db.execute("UPDATE chat_turns SET job = '{}' WHERE id = ?", (message_id,))

    def check(running, queued):
        counts = {"running": running, "queued": queued}
        assert client.get("/api/chats").json()["activity"] == {
            **counts,
            "agents": {"ada": counts} if running or queued else {},
        }

    check(1, 3)
    assert db.consume_chat_input("active", "steer")
    check(1, 2)
    assert db.stop_chat_turn(chat_id, "active")
    # Stop requested is still running until the agent actually finishes cleanup.
    check(1, 2)
    db.finish_chat_turn("active", "Stopped", {})
    check(0, 2)
    assert client.delete(f"/api/chats/{chat_id}/queue/discard").status_code == 200
    assert events[-1] == ("chat.queue.changed", {"chat_id": chat_id})
    check(0, 1)
    assert client.post(f"/api/chats/{chat_id}/queue/resume").status_code == 200
    assert spawned and events[-1] == ("chat.queue.changed", {"chat_id": chat_id})
    check(1, 0)
    db.finish_chat_turn("next", "Done", {})
    check(0, 0)
    db.delete_chat(chat_id)
    check(0, 0)


async def test_consumed_input_refreshes_other_clients_without_waiting_for_progress(backend, monkeypatch):
    db = backend.db
    chat_id = db.create_chat("Ada", "default", agent_id="ada")["id"]
    db.accept_chat_message(chat_id, "Run", "active")
    db.accept_chat_message(chat_id, "Steer", "steer", queue=True)
    events = []
    monkeypatch.setattr(conversations.bus, "publish", lambda kind, data: events.append((kind, data)))

    async def agent(job):
        yield {"type": "delta", "text": "Before steering"}
        yield {"type": "input_consumed", "id": "steer"}
        assert db.chat_activity() == {
            "running": 1,
            "queued": 0,
            "agents": {"ada": {"running": 1, "queued": 0}},
        }
        assert ("chat.queue.changed", {"chat_id": chat_id}) in events
        yield {"type": "input_consumed", "id": "steer"}  # duplicate acknowledgement
        assert sum(kind == "chat.queue.changed" for kind, _ in events) == 1
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(conversations, "stream_agent", agent)
    await conversations.run_turn("active", chat_id, {"prompt": "Run"})
    assert db.chat_activity() == {"running": 0, "queued": 0, "agents": {}}
