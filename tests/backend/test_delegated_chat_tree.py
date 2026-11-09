"""Upgrade existing agent chats without touching titles chosen by humans."""

import json
import sqlite3

from nautionette_backend.migrations import add_delegated_chat_tree


def test_legacy_delegation_backfill_is_idempotent_and_preserves_renames():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE chats (id TEXT PRIMARY KEY, title TEXT, agent_name TEXT, "
        "title_state TEXT, title_revision INTEGER);"
        "CREATE TABLE messages (chat_id TEXT, role TEXT, content TEXT, meta TEXT);"
        "CREATE TABLE agent_calls (parent_chat_id TEXT, child_chat_id TEXT);"
    )
    for id_, title, revision in (
        ("legacy", "Lead → Research", 1),
        ("renamed", "A human title", 2),
        ("same", "Lead → Research", 2),
        ("human", "Lead → Research", 1),
    ):
        conn.execute("INSERT INTO chats VALUES (?,?,?,'manual',?)", (id_, title, "Research", revision))
        conn.execute(
            "INSERT INTO messages VALUES (?,'user',?,?)",
            (id_, "Investigate\n  deployment failures", json.dumps({"sender_agent": {"name": "Lead"}})),
        )
        if id_ != "human":
            conn.execute("INSERT INTO agent_calls VALUES ('root',?)", (id_,))
    # Multiple turns continuing one child must not create duplicates or change ownership.
    conn.execute("INSERT INTO agent_calls VALUES ('root','legacy')")
    add_delegated_chat_tree(conn)
    rows = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM chats")}
    assert rows["legacy"]["title"] == "Investigate deployment failures"
    assert rows["legacy"]["title_state"] == "provisional"
    assert rows["legacy"]["delegated_parent_chat_id"] == "root"
    assert rows["renamed"]["title"] == "A human title"
    assert rows["same"]["title"] == "Lead → Research"
    assert rows["human"]["delegated_parent_chat_id"] is None
    assert rows["human"]["title"] == "Lead → Research"
    add_delegated_chat_tree(conn)
    assert rows == {row["id"]: dict(row) for row in conn.execute("SELECT * FROM chats")}
    conn.close()


def test_chat_page_includes_siblings_and_handles_deleted_parents_and_cycles(db):
    # Use real database selection, independent of call/turn retention.
    root = db.create_chat(title="Root", model="test", agent_set="default")
    child = db.create_chat(title="Child", model="test", agent_set="default")
    sibling = db.create_chat(title="Sibling", model="test", agent_set="default")
    unrelated = db.create_chat(title="Unrelated", model="test", agent_set="default")
    for chat in (child, sibling):
        db.execute("UPDATE chats SET delegated_parent_chat_id=? WHERE id=?", (root["id"], chat["id"]))
    db.execute("UPDATE chats SET last_activity_at=1")
    db.execute("UPDATE chats SET last_activity_at=100 WHERE id=?", (child["id"],))
    assert {chat["id"] for chat in db.list_chats(limit=1)} == {root["id"], child["id"], sibling["id"]}
    # A missing ancestor must not join an unrelated recent conversation.
    db.delete_chat(root["id"])
    assert {chat["id"] for chat in db.list_chats(limit=1)} == {child["id"], sibling["id"]}
    db.execute("UPDATE chats SET delegated_parent_chat_id=? WHERE id=?", (sibling["id"], child["id"]))
    db.execute("UPDATE chats SET delegated_parent_chat_id=? WHERE id=?", (child["id"], sibling["id"]))
    assert {chat["id"] for chat in db.list_chats(limit=1)} == {child["id"], sibling["id"]}
    assert unrelated["id"] not in {chat["id"] for chat in db.list_chats(limit=1)}
