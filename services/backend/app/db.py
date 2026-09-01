"""SQLite storage: chats, messages, runs and approvals.

PocketBase-shaped in spirit (one file, no server), plain sqlite3 in practice.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    agent_set TEXT NOT NULL DEFAULT 'default',
    model TEXT,
    tools TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    promoted_to TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, created_at);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    workflow TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    run_id TEXT,
    trigger TEXT NOT NULL,
    input TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'started',
    result TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_workflow ON runs(workflow, created_at);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_scope ON events(scope, id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Applied on every start; each one fails harmlessly once it is already in place.
_MIGRATIONS = (
    "ALTER TABLE chats ADD COLUMN model TEXT",
    "ALTER TABLE chats ADD COLUMN tools TEXT",
)


def _dump_tools(tools: list[str] | None) -> str | None:
    """None means every tool the gateway federates; a list narrows it."""
    return None if tools is None else json.dumps(sorted(set(tools)))


class Database:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            for statement in _MIGRATIONS:
                try:
                    self._conn.execute(statement)
                except sqlite3.OperationalError:
                    pass  # already applied
            self._conn.commit()

    # ------------------------------------------------------------------ basics

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------- chats

    def create_chat(
        self,
        title: str,
        agent_set: str,
        model: str | None = None,
        tools: list[str] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        chat_id = uuid.uuid4().hex[:12]
        self.execute(
            "INSERT INTO chats (id, title, agent_set, model, tools, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (chat_id, title, agent_set, model, _dump_tools(tools), now, now),
        )
        return self.get_chat(chat_id)  # type: ignore[return-value]

    def update_chat(self, chat_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {k: v for k, v in fields.items() if k in {"title", "agent_set", "model", "tools"}}
        if "tools" in allowed:
            allowed["tools"] = _dump_tools(allowed["tools"])
        if allowed:
            assignments = ", ".join(f"{key} = ?" for key in allowed)
            self.execute(
                f"UPDATE chats SET {assignments} WHERE id = ?", (*allowed.values(), chat_id)
            )
        return self.get_chat(chat_id)

    def get_chat(self, chat_id: str) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM chats WHERE id = ?", (chat_id,))
        if row:
            row["tools"] = json.loads(row["tools"]) if row.get("tools") else None
        return row

    def list_chats(self) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM chats ORDER BY updated_at DESC LIMIT 200")
        for row in rows:
            counts = self.one("SELECT COUNT(*) AS n FROM messages WHERE chat_id = ?", (row["id"],))
            row["message_count"] = counts["n"] if counts else 0
            last = self.one(
                "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY created_at DESC"
                " LIMIT 1",
                (row["id"],),
            )
            row["last_message"] = (
                {"role": last["role"], "preview": " ".join(last["content"].split())[:120]}
                if last
                else None
            )
        return rows

    def touch_chat(self, chat_id: str) -> None:
        self.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (time.time(), chat_id))

    def delete_chat(self, chat_id: str) -> None:
        self.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        self.execute("DELETE FROM chats WHERE id = ?", (chat_id,))

    def add_message(
        self, chat_id: str, role: str, content: str, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        now = time.time()
        message_id = uuid.uuid4().hex[:12]
        self.execute(
            "INSERT INTO messages (id, chat_id, role, content, meta, created_at) VALUES (?,?,?,?,?,?)",
            (message_id, chat_id, role, content, json.dumps(meta or {}), now),
        )
        self.touch_chat(chat_id)
        return {
            "id": message_id,
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "meta": meta or {},
            "created_at": now,
        }

    def list_messages(self, chat_id: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at ASC LIMIT ?",
            (chat_id, limit),
        )
        for row in rows:
            row["meta"] = json.loads(row["meta"] or "{}")
        return rows

    # -------------------------------------------------------------------- runs

    def record_run(
        self,
        workflow: str,
        workflow_id: str,
        run_id: str | None,
        trigger: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = time.time()
        row_id = uuid.uuid4().hex[:12]
        self.execute(
            "INSERT INTO runs (id, workflow, workflow_id, run_id, trigger, input, status, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                row_id,
                workflow,
                workflow_id,
                run_id,
                trigger,
                json.dumps(payload),
                "running",
                now,
                now,
            ),
        )
        return self.one("SELECT * FROM runs WHERE id = ?", (row_id,))  # type: ignore[return-value]

    def update_run(self, workflow_id: str, status: str, result: Any = None) -> None:
        self.execute(
            "UPDATE runs SET status = ?, result = ?, updated_at = ? WHERE workflow_id = ?",
            (status, json.dumps(result) if result is not None else None, time.time(), workflow_id),
        )

    def list_runs(self, workflow: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if workflow:
            rows = self.query(
                "SELECT * FROM runs WHERE workflow = ? ORDER BY created_at DESC LIMIT ?",
                (workflow, limit),
            )
        else:
            rows = self.query("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))
        for row in rows:
            row["input"] = json.loads(row["input"] or "{}")
            row["result"] = json.loads(row["result"]) if row["result"] else None
        return rows

    # ------------------------------------------------------------------ events

    def add_event(self, scope: str, kind: str, payload: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO events (scope, kind, payload, created_at) VALUES (?,?,?,?)",
            (scope, kind, json.dumps(payload), time.time()),
        )
        self.execute("DELETE FROM events WHERE id < (SELECT MAX(id) - 5000 FROM events)")

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            row["payload"] = json.loads(row["payload"])
        return list(reversed(rows))

    # ---------------------------------------------------------------- settings

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, json.dumps(value), json.dumps(value)),
        )

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default
