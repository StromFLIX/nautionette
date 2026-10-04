"""Internet access is unconditional; retire legacy approval state on upgrade."""

import json

import pytest
from nautionette_backend.db import Database


def test_chats_and_jobs_have_no_network_permission(client, broker):
    chat = client.post("/api/chats", json={"internet_status": "denied"}).json()
    path = f"/api/chats/{chat['id']}"
    assert not any(key.startswith("internet_") for key in chat)
    updated = client.patch(path, json={"internet_status": "pending"}).json()
    assert not any(key.startswith("internet_") for key in updated)
    for _ in range(2):
        response = client.post(path + "/messages", json={"text": "Fetch docs", "internet_allowed": False})
        assert response.status_code == 200
        job = broker.jobs[-1]
        assert not any(key.startswith("internet_") for key in job)
        assert "request_internet_access" not in job["system_prompt"]
        assert "Direct internet access is" not in job["system_prompt"]
    assert not any(key.startswith("internet_") for key in client.get(path).json()["chat"])
    assert client.post(path + "/internet", json={"allowed": True, "turn_id": "old"}).status_code in {404, 405}


@pytest.mark.parametrize("status", ["blocked", "pending", "deciding", "denied", "allowed"])
def test_upgrade_removes_state_and_queued_instructions_without_losing_work(tmp_path, status):
    path = str(tmp_path / "legacy.sqlite3")
    old = Database(path)
    for column in ("internet_status", "internet_reason", "internet_turn_id"):
        old.execute(f"ALTER TABLE chats ADD COLUMN {column} TEXT")
    chat = old.create_chat("Keep this chat", "default", project_ids=["project"])
    old.execute("UPDATE chats SET internet_status = ? WHERE id = ?", (status, chat["id"]))
    old.accept_chat_message(chat["id"], "Keep this message", "turn")
    prompt = (
        "Base instructions.\nSelected writable per-chat Git worktrees:\n/project\n"
        "Stay detached. Worktrees start from the local clone. "
        "Request internet access before direct GitHub connections from the agent container. "
        "Configured tools exposed through agentgateway do not require this approval. "
        "After approval, use Git directly with the configured HTTPS origin. "
        "Keep credential guidance. "
        "If Git reports a DNS/network failure while internet access is blocked, call "
        "request_internet_access and wait; after approval retry the authorized Git operation. "
        "Do not tunnel Git commands through MCP to evade the direct-egress gate, "
        "or ask the user to push manually before requesting access. "
        "Commit and push when asked.\n"
        f"Direct internet access is {status} for this chat. "
        "Before making such a connection, call request_internet_access. "
        "Only direct internet approval is an exception to routine permission-free operation."
        "\nKeep trailing instructions."
    )
    job = {
        "internet_status": status,
        "internet_allowed": status == "allowed",
        "system_prompt": prompt,
        "prompt": "Keep this message",
        "project_ids": ["project"],
        "model": "test/model",
    }
    old.execute("UPDATE chat_turns SET job = ?, state = 'queued' WHERE id = 'turn'", (json.dumps(job),))
    other = old.create_chat("Unrelated", "default")
    old.accept_chat_message(other["id"], "Another job", "other-turn")
    other_job = {"prompt": "Another job", "tools": [], "system_prompt": "Keep unrelated instructions."}
    old.execute("UPDATE chat_turns SET job = ? WHERE id = 'other-turn'", (json.dumps(other_job),))
    old._conn.close()

    for _ in range(2):  # The migration is safe on repeated startup.
        upgraded = Database(path)
        try:
            saved = upgraded.get_chat(chat["id"])
            assert not any(key.startswith("internet_") for key in saved)
            assert saved["title"] == "Keep this chat"
            assert saved["project_ids"] == ["project"]
            assert upgraded.list_messages(chat["id"])[0]["content"] == "Keep this message"
            turn = upgraded.one("SELECT * FROM chat_turns WHERE id = 'turn'")
            assert turn["state"] == "queued"
            migrated = json.loads(turn["job"])
            assert (
                json.loads(upgraded.one("SELECT job FROM chat_turns WHERE id = 'other-turn'")["job"])
                == other_job
            )
            assert migrated == {
                "prompt": "Keep this message",
                "project_ids": ["project"],
                "model": "test/model",
                "system_prompt": (
                    "Base instructions.\nSelected writable per-chat Git worktrees:\n/project\n"
                    "Stay detached. Worktrees start from the local clone. "
                    "Use Git directly with the configured HTTPS origin. Keep credential guidance. "
                    "Commit and push when asked.\nKeep trailing instructions."
                ),
            }
        finally:
            upgraded._conn.close()
