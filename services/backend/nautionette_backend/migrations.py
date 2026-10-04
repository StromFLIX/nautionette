"""One-time data cleanup for retired features."""

from __future__ import annotations

import json
import re
import sqlite3


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
