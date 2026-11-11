"""Demand-driven OAuth gates. No tool arguments, tokens or results are stored here.

The gate runs BEFORE dispatch: resuming it never replays a possibly-run mutation.
Waits are durable, turn-bound and bounded even when no UI is connected.
"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import HTTPException

from . import chat_outputs, mcp_http_oauth
from .db import db
from .events import bus

WAIT_SECONDS = 180


def remember_tools(tools: list[dict]) -> None:
    servers = mcp_http_oauth.public_connections()
    for tool in tools:
        if tool.get("server") in servers:
            schema = {key: value for key, value in tool.items() if key != "server"}
            db.execute(
                "INSERT INTO mcp_oauth_tools VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "server=excluded.server,schema=excluded.schema",
                (tool["name"], tool["server"], json.dumps(schema)),
            )


def unavailable_tools() -> list[dict]:
    servers = mcp_http_oauth.public_connections()
    return [
        {**json.loads(row["schema"]), "server": row["server"]}
        for row in db.query("SELECT * FROM mcp_oauth_tools")
        if servers.get(row["server"], {}).get("status") != "connected" and row["server"] in servers
    ]


def gate(payload: dict) -> dict:
    if not payload.get("grant"):
        # Non-chat agents may use healthy connections, but cannot create an
        # unowned interactive prompt or hold a workflow indefinitely.
        owner = db.one("SELECT server FROM mcp_oauth_tools WHERE name=?", (payload["tool"],))
        connection = mcp_http_oauth.public_connections().get(owner["server"]) if owner else None
        ready = (
            not owner
            or connection
            and connection["status"] == "connected"
            and connection["expires_at"] > time.time()
        )
        return {"status": "ready" if ready else "failed"}
    chat_id, turn_id = chat_outputs.grant_scope(payload["grant"])
    with db.transaction() as conn:
        chat_outputs._active(conn, (chat_id, turn_id))
    tool, call_id = payload["tool"], payload["tool_call_id"]
    wait = db.one("SELECT * FROM mcp_oauth_waits WHERE turn_id=? AND tool_call_id=?", (turn_id, call_id))
    if wait and wait["tool"] != tool:
        raise HTTPException(409, "OAuth gate tool call mismatch")
    if payload.get("cancel"):
        if wait:
            reject([wait["id"]])
        return {"status": "failed"}
    if wait and (wait["state"] == "failed" or wait["deadline"] <= time.time()):
        if wait["state"] == "waiting":
            db.execute("UPDATE mcp_oauth_waits SET state='failed' WHERE id=?", (wait["id"],))
            bus.publish("mcp.oauth.resolved", {"server": wait["server"]})
        return {
            "status": "failed",
            "message": "MCP sign-in skipped, snoozed or timed out; tool was not called.",
        }
    owner = db.one("SELECT server FROM mcp_oauth_tools WHERE name=?", (tool,))
    server = wait["server"] if wait else owner["server"] if owner else None
    if not server:
        return {"status": "ready"}
    connection = mcp_http_oauth.public_connections().get(server)
    if not connection:
        return {"status": "failed", "message": "MCP connection removed; tool was not called."}
    if connection["status"] == "connected" and connection["expires_at"] > time.time():
        # Connected is published only after a fresh federated MCP handshake and
        # tool discovery succeed, not merely when token/config storage succeeds.
        if wait and wait["state"] == "waiting":
            db.execute("UPDATE mcp_oauth_waits SET state='ready' WHERE id=?", (wait["id"],))
            bus.publish("mcp.oauth.resolved", {"server": server})
        return {"status": "ready"}
    if not wait:
        identifier = uuid.uuid4().hex
        db.execute(
            "INSERT INTO mcp_oauth_waits VALUES (?,?,?,?,?,?,?)",
            (identifier, turn_id, call_id, tool, server, time.time() + WAIT_SECONDS, "waiting"),
        )
        bus.publish("mcp.oauth.requested", {"server": server})
    return {"status": "waiting", "server": server}


def pending() -> list[dict]:
    # Completed/stopped turns and timed-out gates cannot cause login prompts.
    return db.query(
        "SELECT w.id,w.server,w.deadline FROM mcp_oauth_waits w "
        "JOIN chat_turns t ON t.id=w.turn_id WHERE w.state='waiting' AND w.deadline>? "
        "AND t.state='running' AND t.stop_requested=0 "
        "ORDER BY w.deadline",
        (time.time(),),
    )


def reject(identifiers: list[str]) -> None:
    for identifier in identifiers:
        db.execute(
            "UPDATE mcp_oauth_waits SET state='failed' WHERE id=? AND state='waiting'",
            (identifier,),
        )
    bus.publish("mcp.oauth.resolved", {})
