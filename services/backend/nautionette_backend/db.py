"""SQLite storage: chats, messages, runs and settings.

PocketBase-shaped in spirit (one file, no server), plain sqlite3 in practice.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import settings
from .migrations import add_agent_grouping, remove_internet_approval
from .output_ids import publication_request_id
from .search_index import install as install_search_index
from .search_index import load_extension as load_search_extension

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
CREATE TABLE IF NOT EXISTS chat_images (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    data BLOB NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_images_chat ON chat_images(chat_id);
CREATE TABLE IF NOT EXISTS chat_outputs (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    current_revision INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_outputs_chat ON chat_outputs(chat_id, updated_at);
CREATE TABLE IF NOT EXISTS chat_output_revisions (
    output_id TEXT NOT NULL REFERENCES chat_outputs(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    turn_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    manifest TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (output_id, revision),
    UNIQUE (turn_id, request_id)
);
CREATE TABLE IF NOT EXISTS chat_output_files (
    id TEXT PRIMARY KEY,
    output_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    role TEXT NOT NULL,
    size INTEGER NOT NULL,
    data BLOB,
    preview BLOB,
    object_key TEXT,
    preview_key TEXT,
    preview_size INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT,
    FOREIGN KEY (output_id, revision) REFERENCES chat_output_revisions(output_id, revision) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS chat_output_files_revision ON chat_output_files(output_id, revision);
-- An object is registered BEFORE contacting S3. Cascades enqueue deletion in
-- the same metadata transaction, so a crash or unavailable S3 cannot leak it.
CREATE TABLE IF NOT EXISTS output_storage_objects (
    key TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    delete_after REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_output_uploads (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    turn_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    reserved_bytes INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    UNIQUE(turn_id, request_id)
);
CREATE TABLE IF NOT EXISTS chat_output_upload_files (
    upload_id TEXT NOT NULL REFERENCES chat_output_uploads(id) ON DELETE CASCADE,
    object_key TEXT NOT NULL,
    PRIMARY KEY(upload_id, object_key)
);
CREATE TRIGGER IF NOT EXISTS output_upload_file_deleted AFTER DELETE ON chat_output_upload_files BEGIN
    UPDATE output_storage_objects SET state = 'delete' WHERE key = OLD.object_key;
END;
CREATE TABLE IF NOT EXISTS output_cleanup_plans (
    id TEXT PRIMARY KEY,
    items TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_turns (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'running',
    steps TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS chat_turn_running ON chat_turns(chat_id) WHERE state = 'running';
CREATE INDEX IF NOT EXISTS chat_turn_activity ON chat_turns(state, chat_id)
    WHERE state IN ('running', 'queued');
CREATE TABLE IF NOT EXISTS chat_turn_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_turn_events_turn ON chat_turn_events(turn_id, seq);
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
CREATE INDEX IF NOT EXISTS runs_workflow_id ON runs(workflow_id);
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
CREATE TABLE IF NOT EXISTS agent_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    config TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pi_package_installations (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    allow_scripts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pi_package_revisions (
    id TEXT PRIMARY KEY,
    installation_id TEXT NOT NULL REFERENCES pi_package_installations(id),
    filters TEXT NOT NULL DEFAULT '{}',
    configuration TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_settings (
    name TEXT PRIMARY KEY,
    disabled INTEGER NOT NULL DEFAULT 0,
    chat_mode TEXT NOT NULL DEFAULT 'same',
    chat_id TEXT
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    repository_id INTEGER NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    default_branch TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_leases (
    project_id TEXT NOT NULL REFERENCES projects(id),
    turn_id TEXT NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, turn_id)
);
CREATE TABLE IF NOT EXISTS github_project_connections (
    id TEXT PRIMARY KEY,
    config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS github_app_setups (
    state_hash TEXT PRIMARY KEY,
    browser_hash TEXT NOT NULL DEFAULT '',
    phase TEXT NOT NULL,
    expires_at REAL NOT NULL,
    public_url TEXT NOT NULL,
    organization TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS github_issue_chats (
    thread_key TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS github_mentions (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS github_webhook_deliveries (
    id TEXT PRIMARY KEY,
    received_at REAL NOT NULL
);
"""

# Applied on every start; each one fails harmlessly once it is already in place.
_MIGRATIONS = (
    "ALTER TABLE projects ADD COLUMN connection_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE pi_package_installations ADD COLUMN default_revision_id TEXT",
    "ALTER TABLE chat_turns ADD COLUMN context TEXT",
    "ALTER TABLE chat_turns ADD COLUMN timing TEXT",
    "ALTER TABLE chat_turns ADD COLUMN job TEXT",
    "ALTER TABLE chat_turns ADD COLUMN stop_requested INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE chats ADD COLUMN pi_session_id TEXT",
    "ALTER TABLE chats ADD COLUMN queue_paused INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE chats ADD COLUMN timeout_exempt INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE chats ADD COLUMN model TEXT",
    "ALTER TABLE chats ADD COLUMN reasoning_effort TEXT",
    "ALTER TABLE chats ADD COLUMN agent_id TEXT",
    "ALTER TABLE chats ADD COLUMN agent_name TEXT",
    "ALTER TABLE chats ADD COLUMN system_prompt TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE agent_profiles ADD COLUMN image TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE chats ADD COLUMN packages TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE chats ADD COLUMN package_commands TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE chats ADD COLUMN tools TEXT",
    "ALTER TABLE chats ADD COLUMN project_ids TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE chats ADD COLUMN last_read_message_id TEXT",
    "ALTER TABLE chats ADD COLUMN marked_unread INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE chats ADD COLUMN read_revision INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE chats ADD COLUMN last_message_at REAL",
    "ALTER TABLE chats ADD COLUMN last_user_message_at REAL",
    "ALTER TABLE chats ADD COLUMN last_activity_at REAL",
    # Legacy titles have no provenance: protect them rather than guessing which were manual.
    "ALTER TABLE chats ADD COLUMN title_state TEXT NOT NULL DEFAULT 'manual'",
    "ALTER TABLE chats ADD COLUMN title_revision INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE chat_outputs ADD COLUMN next_revision INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE chat_output_files ADD COLUMN object_key TEXT",
    "ALTER TABLE chat_output_files ADD COLUMN preview_key TEXT",
    "ALTER TABLE chat_output_files ADD COLUMN preview_size INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE chat_output_files ADD COLUMN sha256 TEXT",
)

_EDITABLE_CHAT_COLUMNS = (
    "title",
    "agent_set",
    "model",
    "reasoning_effort",
    "tools",
    "project_ids",
    "agent_id",
    "agent_name",
    "agent_customized",
    "system_prompt",
    "packages",
    "timeout_exempt",
)
_EDITABLE_WORKFLOW_COLUMNS = ("disabled", "chat_mode", "chat_id")

WORKFLOW_DEFAULTS = {"disabled": False, "chat_mode": "same", "chat_id": None}

# Only incoming, saved replies count as unread; queue edits and user messages do not.
_CHAT_UNREAD = """(marked_unread = 1 OR EXISTS (
    SELECT 1 FROM messages m WHERE m.chat_id = chats.id AND m.role = 'assistant'
    AND m.rowid > COALESCE((SELECT rowid FROM messages WHERE id = chats.last_read_message_id), 0)
)) AS unread"""


def _dump_tools(tools: list[str] | None) -> str | None:
    """None means every tool the gateway federates; a list narrows it."""
    return None if tools is None else json.dumps(sorted(set(tools)))


class Database:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        load_search_extension(self._conn)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            for statement in _MIGRATIONS:
                try:
                    self._conn.execute(statement)
                except sqlite3.OperationalError:
                    pass  # already applied
            self._conn.execute(
                "UPDATE chat_outputs SET next_revision = MAX(next_revision, "
                "COALESCE((SELECT MAX(revision) + 1 FROM chat_output_revisions "
                "WHERE output_id = chat_outputs.id), 1))"
            )
            # Old installations required a BLOB. Rebuild only that table so a
            # verified S3 migration can release bytes with NULL, not retain them.
            columns = self._conn.execute("PRAGMA table_info(chat_output_files)").fetchall()
            if any(column["name"] == "data" and column["notnull"] for column in columns):
                self._conn.execute("ALTER TABLE chat_output_files RENAME TO chat_output_files_legacy")
                self._conn.execute(
                    "CREATE TABLE chat_output_files (id TEXT PRIMARY KEY, output_id TEXT NOT NULL, "
                    "revision INTEGER NOT NULL, name TEXT NOT NULL, "
                    "mime_type TEXT NOT NULL, role TEXT NOT NULL, "
                    "size INTEGER NOT NULL, data BLOB, preview BLOB, object_key TEXT, preview_key TEXT, "
                    "preview_size INTEGER NOT NULL DEFAULT 0, sha256 TEXT, "
                    "FOREIGN KEY (output_id, revision) REFERENCES chat_output_revisions(output_id, revision) "
                    "ON DELETE CASCADE)"
                )
                self._conn.execute("INSERT INTO chat_output_files SELECT * FROM chat_output_files_legacy")
                self._conn.execute("DROP TABLE chat_output_files_legacy")
                self._conn.execute(
                    "CREATE INDEX chat_output_files_revision ON chat_output_files(output_id, revision)"
                )
            self._conn.executescript(
                "CREATE TRIGGER IF NOT EXISTS output_file_deleted AFTER DELETE ON chat_output_files BEGIN "
                "UPDATE output_storage_objects SET state = 'delete', delete_after = 0 "
                "WHERE key IN (OLD.object_key, OLD.preview_key); END;"
            )
            remove_internet_approval(self._conn)
            add_agent_grouping(self._conn, settings.agent_model, settings.default_agent_set)
            # Backfill only legacy rows, never overwrite live message timestamps on restart.
            self._conn.execute(
                "UPDATE chats SET last_message_at = COALESCE("
                "(SELECT MAX(created_at) FROM messages WHERE chat_id = chats.id), created_at) "
                "WHERE last_message_at IS NULL"
            )
            self._conn.execute(
                "UPDATE chats SET last_user_message_at = COALESCE("
                "(SELECT MAX(created_at) FROM messages WHERE chat_id = chats.id AND role = 'user'), "
                "created_at) WHERE last_user_message_at IS NULL"
            )
            # Saved messages are the best available activity boundaries for legacy chats.
            # Never seed from updated_at/last_message_at, which include live progress.
            self._conn.execute(
                "UPDATE chats SET last_activity_at = COALESCE("
                "(SELECT MAX(created_at) FROM messages WHERE chat_id = chats.id "
                "AND role IN ('user', 'assistant')), created_at) WHERE last_activity_at IS NULL"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS chats_activity ON chats(last_activity_at DESC, id)"
            )
            lease_columns = self._conn.execute("PRAGMA table_info(project_leases)").fetchall()
            if [column["name"] for column in lease_columns if column["pk"]] == ["project_id"]:
                self._conn.execute("ALTER TABLE project_leases RENAME TO project_leases_legacy")
                self._conn.execute(
                    "CREATE TABLE project_leases (project_id TEXT NOT NULL REFERENCES projects(id), "
                    "turn_id TEXT NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE, "
                    "PRIMARY KEY (project_id, turn_id))"
                )
                self._conn.execute("INSERT INTO project_leases SELECT * FROM project_leases_legacy")
                self._conn.execute("DROP TABLE project_leases_legacy")
            # Preserve the former singleton installation and its project bindings.
            legacy = self._conn.execute(
                "SELECT value FROM settings WHERE key = 'github_projects_app'"
            ).fetchone()
            if legacy:
                config = json.loads(legacy["value"])
                if config.get("app_id") and config.get("installation_id"):
                    connection_id = f"{config['app_id']}-{config['installation_id']}"
                    webhook = self._conn.execute(
                        "SELECT value FROM settings WHERE key = 'github_projects_webhook'"
                    ).fetchone()
                    if webhook:
                        config["webhook"] = json.loads(webhook["value"])
                    self._conn.execute(
                        "INSERT OR IGNORE INTO github_project_connections (id, config) VALUES (?,?)",
                        (connection_id, json.dumps(config)),
                    )
                    self._conn.execute(
                        "UPDATE projects SET connection_id = ? WHERE connection_id = ''", (connection_id,)
                    )
                    self._conn.execute(
                        "DELETE FROM settings WHERE key IN ('github_projects_app', 'github_projects_webhook')"
                    )
            install_search_index(self._conn)
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

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialize a complete operation and commit or roll it back atomically."""
        with self._lock, self._conn:
            yield self._conn

    def compact(self) -> None:
        """Explicitly reclaim pages freed by legacy output migration/cleanup."""
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("VACUUM")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def list_chat_outputs(self, chat_id: str) -> list[dict[str, Any]]:
        return [
            json.loads(row["manifest"])
            for row in self.query(
                "SELECT r.manifest FROM chat_outputs o JOIN chat_output_revisions r "
                "ON r.output_id = o.id AND r.revision = o.current_revision "
                "WHERE o.chat_id = ? ORDER BY o.updated_at DESC, o.id",
                (chat_id,),
            )
        ]

    # ------------------------------------------------------------------- chats

    def create_chat(
        self,
        title: str,
        agent_set: str,
        model: str | None = None,
        tools: list[str] | None = None,
        reasoning_effort: str | None = None,
        project_ids: list[str] | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
        packages: list[str] | None = None,
        timeout_exempt: bool = False,
        chat_id: str | None = None,
        system_prompt: str = "",
        agent_customized: bool = False,
    ) -> dict[str, Any]:
        now = time.time()
        chat_id = chat_id or uuid.uuid4().hex[:12]
        self.execute(
            "INSERT INTO chats (id, title, agent_set, model, tools, reasoning_effort,"
            " created_at, updated_at, title_state, project_ids, agent_id, agent_name, packages,"
            " last_message_at, last_user_message_at, last_activity_at, timeout_exempt,"
            " system_prompt, agent_customized)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                chat_id,
                title,
                agent_set,
                model,
                _dump_tools(tools),
                reasoning_effort,
                now,
                now,
                "provisional" if title in {"New chat", ""} else "manual",
                json.dumps(project_ids or []),
                agent_id,
                agent_name,
                json.dumps(packages or []),
                now,
                now,
                now,
                timeout_exempt,
                system_prompt,
                agent_customized,
            ),
        )
        return self.get_chat(chat_id)  # type: ignore[return-value]

    def update_chat(self, chat_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {k: v for k, v in fields.items() if k in _EDITABLE_CHAT_COLUMNS}
        if "tools" in allowed:
            allowed["tools"] = _dump_tools(allowed["tools"])
        for key in ("project_ids", "packages"):
            if key in allowed:
                allowed[key] = json.dumps(allowed[key])
        if allowed:
            # Column names come from the tuple above, never from the caller.
            assignments = ", ".join(f"{key} = ?" for key in allowed)
            if "packages" in allowed or "agent_set" in allowed:
                assignments += ", package_commands = '[]'"
            if "title" in allowed:
                # Even renaming to the same text invalidates an in-flight title request.
                assignments += ", title_state = 'manual', title_revision = title_revision + 1"
            self.execute(
                f"UPDATE chats SET {assignments} WHERE id = ?",  # noqa: S608
                (*allowed.values(), chat_id),
            )
        return self.get_chat(chat_id)

    def get_chat(self, chat_id: str) -> dict[str, Any] | None:
        row = self.one(
            f"SELECT *, {_CHAT_UNREAD} FROM chats WHERE id = ?",  # noqa: S608
            (chat_id,),
        )
        if row:
            row["agent_customized"] = bool(row["agent_customized"])
            row["timeout_exempt"] = bool(row["timeout_exempt"])
            row["unread"] = bool(row["unread"])
            row["tools"] = json.loads(row["tools"]) if row.get("tools") else None
            row["project_ids"] = json.loads(row["project_ids"])
            row["packages"] = json.loads(row["packages"])
            row["package_commands"] = json.loads(row["package_commands"])
        return row

    def list_chats(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.query(
            f"SELECT *, {_CHAT_UNREAD} FROM chats ORDER BY last_activity_at DESC, id LIMIT ?",  # noqa: S608
            (limit,),
        )
        if not rows:
            return rows
        placeholders = ", ".join("?" for _ in rows)
        # SQLite takes the bare columns from the row max() matched, so one pass
        # gives both the count and the newest message for every chat.
        summary_sql = (
            "SELECT chat_id, COUNT(*) AS n, MAX(created_at), role, content"  # noqa: S608
            f" FROM messages WHERE chat_id IN ({placeholders}) GROUP BY chat_id"
        )
        summaries = {
            summary["chat_id"]: summary for summary in self.query(summary_sql, [row["id"] for row in rows])
        }
        answering = {
            turn["chat_id"] for turn in self.query("SELECT chat_id FROM chat_turns WHERE state = 'running'")
        }
        for row in rows:
            summary = summaries.get(row["id"])
            row["agent_customized"] = bool(row["agent_customized"])
            row["timeout_exempt"] = bool(row["timeout_exempt"])
            row["unread"] = bool(row["unread"])
            row["answering"] = row["id"] in answering
            row["tools"] = json.loads(row["tools"]) if row.get("tools") else None
            row["project_ids"] = (
                json.loads(row["project_ids"])
                if isinstance(row.get("project_ids"), str)
                else (row.get("project_ids") or [])
            )
            row["packages"] = json.loads(row["packages"])
            row["package_commands"] = json.loads(row["package_commands"])
            row["message_count"] = summary["n"] if summary else 0
            row["last_message"] = (
                {"role": summary["role"], "preview": " ".join(summary["content"].split())[:120]}
                if summary
                else None
            )
        return rows

    def chat_activity(self) -> dict[str, Any]:
        """Count live turns across all chats, not just the recent sidebar page.

        Customized and unassigned chats belong to All only, just like the agent
        views. Each running turn is one active agent; each queued turn is one
        waiting message, including paused queues. Steered/completed turns are not
        waiting, even when their user messages remain in history.
        """
        activity: dict[str, Any] = {"running": 0, "queued": 0, "agents": {}}
        for row in self.query(
            "SELECT c.agent_id, c.agent_customized, t.state, COUNT(*) AS n "
            "FROM chat_turns t JOIN chats c ON c.id = t.chat_id "
            "WHERE t.state IN ('running', 'queued') "
            "GROUP BY c.agent_id, c.agent_customized, t.state"
        ):
            activity[row["state"]] += row["n"]
            if row["agent_id"] and not row["agent_customized"]:
                counts = activity["agents"].setdefault(row["agent_id"], {"running": 0, "queued": 0})
                counts[row["state"]] += row["n"]
        return activity

    def set_chat_read_state(
        self,
        chat_id: str,
        *,
        unread: bool | None = None,
        message_id: str | None = None,
        revision: int | None = None,
        clear_manual: bool = False,
    ) -> bool:
        """Acknowledge only the reply actually displayed, never a concurrently arriving one.

        Manual changes advance a revision so in-flight acknowledgements cannot undo them.
        Read state never changes the chat's ordering timestamp.
        """
        with self._lock, self._conn:
            if unread is not None:
                return bool(
                    self._conn.execute(
                        "UPDATE chats SET marked_unread = ?, read_revision = read_revision + 1, "
                        "last_read_message_id = CASE WHEN ? THEN last_read_message_id ELSE "
                        "(SELECT id FROM messages WHERE chat_id = ? AND role = 'assistant' "
                        "ORDER BY rowid DESC LIMIT 1) END WHERE id = ?",
                        (int(unread), int(unread), chat_id, chat_id),
                    ).rowcount
                )
            message = None
            if message_id is not None:
                message = self._conn.execute(
                    "SELECT rowid FROM messages WHERE id = ? AND chat_id = ? AND role = 'assistant'",
                    (message_id, chat_id),
                ).fetchone()
                if not message:
                    raise ValueError("message_id must identify an assistant reply in this chat")
            position = message["rowid"] if message else 0
            return bool(
                self._conn.execute(
                    "UPDATE chats SET last_read_message_id = CASE WHEN ? > "
                    "COALESCE((SELECT rowid FROM messages WHERE id = chats.last_read_message_id), 0) "
                    "THEN ? ELSE last_read_message_id END, "
                    "marked_unread = CASE WHEN ? THEN 0 ELSE marked_unread END "
                    "WHERE id = ? AND read_revision = ? AND ("
                    "? > COALESCE((SELECT rowid FROM messages WHERE id = chats.last_read_message_id), 0) "
                    "OR (? AND marked_unread = 1))",
                    (position, message_id, clear_manual, chat_id, revision, position, clear_manual),
                ).rowcount
            )

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
        self.execute(
            "UPDATE chats SET updated_at = ?, last_message_at = ?, "
            "last_user_message_at = CASE WHEN ? = 'user' THEN ? ELSE last_user_message_at END, "
            "last_activity_at = CASE WHEN ? IN ('user', 'assistant') THEN ? ELSE last_activity_at END "
            "WHERE id = ?",
            (now, now, role, now, role, now, chat_id),
        )
        return {
            "id": message_id,
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "meta": meta or {},
            "created_at": now,
        }

    def list_messages(self, chat_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY rowid ASC LIMIT ?",
            (chat_id, limit if limit is not None else -1),
        )
        for row in rows:
            row["meta"] = json.loads(row["meta"] or "{}")
        return rows

    # -------------------------------------------------------------------- runs

    def accept_chat_message(
        self,
        chat_id: str,
        text: str,
        message_id: str,
        project_ids: list[str] | None = None,
        *,
        queue: bool = False,
        attachment_ids: list[str] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT m.* FROM chat_turns t JOIN messages m ON m.id = t.user_id WHERE t.id = ?",
                (message_id,),
            ).fetchone()
            if existing:
                if existing["chat_id"] != chat_id or existing["content"] != text:
                    raise ValueError("message_id was already used for a different message")
                if (
                    project_ids is not None
                    and json.loads(existing["meta"]).get("project_ids", []) != project_ids
                ):
                    raise ValueError("message_id was already used with a different project selection")
                if [a["id"] for a in json.loads(existing["meta"]).get("attachments", [])] != (
                    attachment_ids or []
                ):
                    raise ValueError("message_id was already used with different attachments")
                return {**dict(existing), "meta": json.loads(existing["meta"])}, False
            attachments = []
            for image_id in attachment_ids or []:
                image = self._conn.execute(
                    "SELECT id, name, mime_type, size FROM chat_images "
                    "WHERE id = ? AND chat_id = ? AND message_id IS NULL",
                    (image_id, chat_id),
                ).fetchone()
                if not image:
                    raise ValueError("An attachment is missing or already attached to another message")
                attachments.append(dict(image))
            waiting = (
                queue
                and self._conn.execute(
                    "SELECT 1 FROM chat_turns WHERE chat_id = ? AND state IN ('running', 'queued') "
                    "UNION ALL SELECT 1 FROM chats WHERE id = ? AND queue_paused = 1",
                    (chat_id, chat_id),
                ).fetchone()
            )
            state = "queued" if waiting else "running"
            now = time.time()
            meta = {"project_ids": project_ids} if project_ids else {}
            if attachments:
                meta["attachments"] = attachments
            if waiting:
                meta["queued"] = True
            self._conn.execute(
                "INSERT INTO messages (id, chat_id, role, content, meta, created_at) VALUES (?,?,?,?,?,?)",
                (message_id, chat_id, "user", text, json.dumps(meta), now),
            )
            for image in attachments:
                self._conn.execute(
                    "UPDATE chat_images SET message_id = ? WHERE id = ?", (message_id, image["id"])
                )
            self._conn.execute(
                "INSERT INTO chat_turns (id, chat_id, user_id, state) VALUES (?,?,?,?)",
                (message_id, chat_id, message_id, state),
            )
            for project_id in project_ids or []:
                ready = self._conn.execute(
                    "SELECT id FROM projects WHERE id = ? AND status = 'ready'",
                    (project_id,),
                ).fetchone()
                if not ready:
                    raise ValueError("Selected project is not ready")
                self._conn.execute("INSERT INTO project_leases VALUES (?,?)", (project_id, message_id))
            if project_ids is not None:
                self._conn.execute(
                    "UPDATE chats SET project_ids = ? WHERE id = ?", (json.dumps(project_ids), chat_id)
                )
            self._conn.execute(
                "UPDATE chats SET updated_at = ?, last_message_at = ?, last_user_message_at = ?, "
                "last_activity_at = ? WHERE id = ?",
                (now, now, now, now, chat_id),
            )
            message = {
                "id": message_id,
                "chat_id": chat_id,
                "role": "user",
                "content": text,
                "meta": meta,
                "created_at": now,
            }
            self._conn.execute(
                "INSERT INTO chat_turn_events (turn_id, payload) VALUES (?,?)",
                (message_id, json.dumps({"type": "user_message", "message": message})),
            )
        return message, True

    def chat_snapshot(self, chat_id: str) -> dict[str, Any]:
        turn = self.one("SELECT * FROM chat_turns WHERE chat_id = ? AND state = 'running'", (chat_id,))
        if turn:
            turn.pop("job", None)
            turn["steps"] = json.loads(turn["steps"])
            turn["context"] = json.loads(turn["context"]) if turn["context"] else None
            turn["timing"] = json.loads(turn["timing"]) if turn["timing"] else None
        return {
            "chat": self.get_chat(chat_id),
            "messages": self.list_messages(chat_id),
            "active_turn": turn,
            "outputs": self.list_chat_outputs(chat_id),
        }

    def next_chat_turn(self, chat_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn:
            if self._conn.execute(
                "SELECT 1 FROM chats WHERE id = ? AND queue_paused = 1 "
                "UNION ALL SELECT 1 FROM chat_turns WHERE chat_id = ? AND state = 'running'",
                (chat_id, chat_id),
            ).fetchone():
                return None
            turn = self._conn.execute(
                "SELECT * FROM chat_turns WHERE chat_id = ? AND state = 'queued' ORDER BY rowid LIMIT 1",
                (chat_id,),
            ).fetchone()
            if not turn or not turn["job"]:
                return None
            self._conn.execute("UPDATE chat_turns SET state = 'running' WHERE id = ?", (turn["id"],))
            self._conn.execute(
                "UPDATE messages SET meta = json_remove(meta, '$.queued'), "
                "rowid = (SELECT COALESCE(MAX(rowid), 0) + 1 FROM messages) WHERE id = ?",
                (turn["id"],),
            )
            return dict(turn)

    def consume_chat_input(
        self, turn_id: str, message_id: str, content: str = "", meta: dict[str, Any] | None = None
    ) -> bool:
        """Commit the preceding answer and its consumed input as one transcript boundary."""
        with self._lock, self._conn:
            changed = self._conn.execute(
                "UPDATE chat_turns SET state = 'steered' WHERE id = ? AND state = 'queued' "
                "AND chat_id = (SELECT chat_id FROM chat_turns WHERE id = ? AND state = 'running')",
                (message_id, turn_id),
            ).rowcount
            if changed:
                turn = self._conn.execute("SELECT * FROM chat_turns WHERE id = ?", (turn_id,)).fetchone()
                if content or (meta or {}).get("steps"):
                    self._append_chat_answer(turn, content, meta or {})
                # Inputs are inserted when queued, not when consumed. Move this one
                # after the preceding answer, leaving unconsumed inputs in the queue.
                self._conn.execute(
                    "UPDATE messages SET meta = json_remove(meta, '$.queued'), "
                    "rowid = (SELECT COALESCE(MAX(rowid), 0) + 1 FROM messages) WHERE id = ?",
                    (message_id,),
                )
                self._conn.execute(
                    "UPDATE chat_turns SET steps = '[]', status = '', timing = NULL WHERE id = ?", (turn_id,)
                )
                self._conn.execute("DELETE FROM project_leases WHERE turn_id = ?", (message_id,))
            return bool(changed)

    def stop_chat_turn(self, chat_id: str, turn_id: str) -> bool:
        with self._lock, self._conn:
            changed = self._conn.execute(
                "UPDATE chat_turns SET stop_requested = 1 WHERE id = ? AND chat_id = ? AND state = 'running'",
                (turn_id, chat_id),
            ).rowcount
            if changed:
                self._conn.execute("UPDATE chats SET queue_paused = 1 WHERE id = ?", (chat_id,))
            return bool(changed)

    def record_chat_progress(
        self,
        turn_id: str,
        event: dict[str, Any],
        steps: list,
        status: str,
        timing: dict[str, Any] | None = None,
    ) -> bool:
        """Persist live activity for snapshots/filtering without advancing list order."""
        with self._lock, self._conn:
            if event.get("type") in {"usage", "result"} and "context" in event:
                self._conn.execute(
                    "UPDATE chat_turns SET context = ? WHERE id = ?",
                    (json.dumps(event["context"]), turn_id),
                )
            self._conn.execute(
                "UPDATE chat_turns SET steps = ?, status = ?, timing = COALESCE(?, timing) WHERE id = ?",
                (json.dumps(steps), status, json.dumps(timing) if timing is not None else None, turn_id),
            )
            self._conn.execute(
                "INSERT INTO chat_turn_events (turn_id, payload) VALUES (?,?)",
                (turn_id, json.dumps(event)),
            )
            now = time.time()
            is_message = event.get("type") == "delta" and bool(event.get("text"))
            return bool(
                self._conn.execute(
                    "UPDATE chats SET updated_at = ?, "
                    "last_message_at = CASE WHEN ? THEN ? ELSE last_message_at END "
                    "WHERE id = (SELECT chat_id FROM chat_turns WHERE id = ? AND state = 'running')",
                    (now, is_message, now, turn_id),
                ).rowcount
            )

    def finish_chat_turn(self, turn_id: str, content: str, meta: dict[str, Any]) -> None:
        with self._lock, self._conn:
            turn = self._conn.execute(
                "SELECT * FROM chat_turns WHERE id = ? AND state = 'running'", (turn_id,)
            ).fetchone()
            if not turn:
                return
            message = self._append_chat_answer(turn, content, meta)
            self._conn.execute("UPDATE chat_turns SET state = 'completed' WHERE id = ?", (turn_id,))
            # Only the full turn ending advances recency, not intermediate answer
            # segments saved while consuming queued/steering input.
            self._conn.execute(
                "UPDATE chats SET last_activity_at = ? WHERE id = ?",
                (message["created_at"], turn["chat_id"]),
            )
            self._conn.execute("DELETE FROM project_leases WHERE turn_id = ?", (turn_id,))
            self._conn.execute(
                "INSERT INTO chat_turn_events (turn_id, payload) VALUES (?,?)",
                (turn_id, json.dumps({"type": "done", "message": message})),
            )

    def _append_chat_answer(self, turn: Any, content: str, meta: dict[str, Any]) -> dict[str, Any]:
        """Append an answer segment inside the caller's locked transaction."""
        now = time.time()
        # Keep the latest provider measurement through reloads and interrupted-turn recovery.
        meta = {**meta, "context": json.loads(turn["context"]) if turn["context"] else None}
        # Bind immutable output revisions to the answer segment which published
        # them. A later revision must not change an older response's downloads.
        calls = {
            publication_request_id(step["id"])
            for step in meta.get("steps", [])
            if step.get("kind") == "tool" and isinstance(step.get("id"), str)
        }
        outputs = [
            json.loads(row["manifest"])
            for row in self._conn.execute(
                "SELECT manifest, request_id FROM chat_output_revisions "
                "WHERE turn_id = ? ORDER BY created_at",
                (turn["id"],),
            )
            if row["request_id"] in calls
        ]
        if outputs:
            meta["outputs"] = outputs
        if "timing" not in meta and turn["timing"]:
            # Recovery retains the last measured checkpoint, never backend downtime.
            meta["timing"] = {**json.loads(turn["timing"]), "active": None, "partial": True}
        message = {
            "id": uuid.uuid4().hex[:12],
            "chat_id": turn["chat_id"],
            "role": "assistant",
            "content": content,
            "meta": meta,
            "created_at": now,
        }
        self._conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, meta, created_at) VALUES (?,?,?,?,?,?)",
            (message["id"], turn["chat_id"], "assistant", content, json.dumps(meta), now),
        )
        self._conn.execute(
            "UPDATE chats SET updated_at = ?, last_message_at = ? WHERE id = ?",
            (now, now, turn["chat_id"]),
        )
        return message

    def record_run(
        self,
        workflow: str,
        workflow_id: str,
        run_id: str | None,
        trigger: str,
        payload: dict[str, Any],
        *,
        created_at: float | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        row_id = uuid.uuid4().hex[:12]
        self.execute(
            "INSERT INTO runs (id, workflow, workflow_id, run_id, trigger, input, status, created_at,"
            " updated_at) SELECT ?,?,?,?,?,?,?,?,?"
            " WHERE NOT EXISTS (SELECT 1 FROM runs WHERE workflow_id = ?)",
            (
                row_id,
                workflow,
                workflow_id,
                run_id,
                trigger,
                json.dumps(payload),
                "running",
                created_at if created_at is not None else now,
                now,
                workflow_id,
            ),
        )
        return self.one("SELECT * FROM runs WHERE workflow_id = ?", (workflow_id,))  # type: ignore[return-value]

    def update_run(self, workflow_id: str, status: str, result: Any = None) -> None:
        self.execute(
            "UPDATE runs SET status = ?, result = ?, updated_at = ? WHERE workflow_id = ?",
            (status, json.dumps(result) if result is not None else None, time.time(), workflow_id),
        )

    def unfinished_runs(self) -> list[dict[str, Any]]:
        """Runs this process was following when it stopped."""
        return self.query("SELECT workflow, workflow_id FROM runs WHERE status = 'running'")

    # ---------------------------------------------------------------- settings

    def workflow_settings(self, name: str) -> dict[str, Any]:
        row = self.one("SELECT * FROM workflow_settings WHERE name = ?", (name,))
        if not row:
            return {"name": name, **WORKFLOW_DEFAULTS}
        return {**row, "disabled": bool(row["disabled"])}

    def set_workflow_settings(self, name: str, fields: dict[str, Any]) -> dict[str, Any]:
        allowed = {k: v for k, v in fields.items() if k in _EDITABLE_WORKFLOW_COLUMNS}
        if "disabled" in allowed:
            allowed["disabled"] = int(bool(allowed["disabled"]))
        if allowed:
            # Column names come from the tuple above, never from the caller.
            assignments = ", ".join(f"{key} = excluded.{key}" for key in allowed)
            columns = ", ".join(allowed)
            placeholders = ", ".join("?" for _ in allowed)
            self.execute(
                f"INSERT INTO workflow_settings (name, {columns}) VALUES (?, {placeholders})"  # noqa: S608
                f" ON CONFLICT(name) DO UPDATE SET {assignments}",
                (name, *allowed.values()),
            )
        return self.workflow_settings(name)

    def forget_workflow(self, name: str) -> None:
        self.execute("DELETE FROM workflow_settings WHERE name = ?", (name,))

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

    def save_settings(self, values: dict[str, Any]) -> None:
        """Publish a validated settings edit atomically; null removes an override."""
        with self._lock, self._conn:
            for key, value in values.items():
                if value is None:
                    self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
                else:
                    self._conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, json.dumps(value)),
                    )

    def delete_agent(self, agent_id: str) -> None:
        """Keep existing chat snapshots; fall back to global defaults for new chats."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM agent_profiles WHERE id = ?", (agent_id,))
            self._conn.execute(
                "DELETE FROM settings WHERE key = 'default_agent_id' AND value = ?", (json.dumps(agent_id),)
            )


db = Database(os.path.join(settings.data_dir, "nautionette.db"))
