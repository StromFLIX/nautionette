"""One-time data cleanup for retired features."""

from __future__ import annotations

import json
import re
import sqlite3


def add_agent_grouping(conn: sqlite3.Connection, model: str, agent_set: str) -> None:
    """Classify old custom presets once, never against a profile on every read.

    Afterwards only an explicit agent selection restores membership. Profile or
    default edits cannot move an existing conversation between people.
    """
    if any(row["name"] == "agent_customized" for row in conn.execute("PRAGMA table_info(chats)")):
        return
    conn.execute("ALTER TABLE chats ADD COLUMN agent_customized INTEGER NOT NULL DEFAULT 0")
    base = {
        "model": model,
        "agent_set": agent_set,
        "reasoning_effort": None,
        "tools": None,
        "project_ids": [],
        "packages": [],
        "system_prompt": "",
    }
    for key in ("model", "agent_set", "reasoning_effort", "tools", "project_ids"):
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (f"default_{key}",)).fetchone()
        if row:
            base[key] = json.loads(row["value"])
    agents = {
        row["id"]: json.loads(row["config"]) for row in conn.execute("SELECT id, config FROM agent_profiles")
    }
    for row in conn.execute("SELECT * FROM chats WHERE agent_id IS NOT NULL"):
        overrides = agents.get(row["agent_id"])
        resolved = {**base, **(overrides or {})}
        if resolved["model"] != base["model"] and "reasoning_effort" not in (overrides or {}):
            resolved["reasoning_effort"] = None
        custom = overrides is None
        for key, expected in resolved.items():
            actual = row[key]
            if key in {"tools", "project_ids", "packages"}:
                actual = json.loads(actual) if actual is not None else None
            if key in {"tools", "project_ids"} and isinstance(actual, list) and isinstance(expected, list):
                actual, expected = sorted(actual), sorted(expected)
            custom |= actual != expected
        conn.execute("UPDATE chats SET agent_customized = ? WHERE id = ?", (custom, row["id"]))


def remove_internet_approval(conn: sqlite3.Connection) -> None:
    """Drop legacy state and instructions, including jobs queued before upgrade.

    Preserve transcripts and unrelated job configuration. Column presence makes
    this idempotent; unlike duplicate-column migrations, cleanup errors must fail
    startup rather than leave queued agents waiting on a tool that no longer exists.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(chats)")}
    obsolete = columns & {"internet_status", "internet_reason", "internet_turn_id"}
    if not obsolete:
        return
    for row in conn.execute("SELECT id, job FROM chat_turns WHERE job IS NOT NULL ORDER BY rowid"):
        job = json.loads(row["job"])
        job.pop("internet_status", None)
        job.pop("internet_allowed", None)
        if "system_prompt" in job:
            prompt = re.sub(
                r"\nDirect internet access is .*?"
                r"Only direct internet approval is an exception to routine permission-free operation\.",
                "",
                job["system_prompt"],
                flags=re.DOTALL,
            )
            prompt = re.sub(
                r"Request internet access before direct GitHub connections .*?After approval, use Git",
                "Use Git",
                prompt,
                flags=re.DOTALL,
            )
            prompt = re.sub(
                r"If Git reports a DNS/network failure while internet access is blocked, .*?"
                r"before requesting access\. ",
                "",
                prompt,
                flags=re.DOTALL,
            )
            job["system_prompt"] = prompt
        conn.execute("UPDATE chat_turns SET job = ? WHERE id = ?", (json.dumps(job), row["id"]))
    for column in sorted(obsolete):
        conn.execute(f"ALTER TABLE chats DROP COLUMN {column}")  # noqa: S608 - fixed column allowlist
