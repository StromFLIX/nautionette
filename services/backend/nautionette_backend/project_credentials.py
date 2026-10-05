"""Repository-scoped Git access for the lifetime of one active chat turn."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from . import projects
from .clients import broker
from .db import db

RENEW_BEFORE = 300
RETRY_SECONDS = 15


def expires_at(credential: dict[str, Any]) -> float:
    return datetime.fromisoformat(credential["expires_at"]).timestamp()


class TurnCredentials:
    def __init__(self, chat_id: str, turn_id: str, project_ids: list[str]) -> None:
        self.chat_id = chat_id
        self.turn_id = turn_id
        # Steering or edits to a chat's selection must never expand this turn's scope.
        self.project_ids = tuple(project_ids)
        self.current: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.issued: list[dict[str, Any]] = []

    async def issue(self) -> list[dict[str, Any]]:
        # Also prune during prolonged delivery outages, not only on success.
        self.issued = [item for item in self.issued if expires_at(item) > time.time()]
        credentials = await projects.agent_credentials(list(self.project_ids))
        self.issued.extend(credentials)
        return credentials

    async def start(self) -> list[dict[str, Any]]:
        self.current = await self.issue()
        return self.current

    def active(self) -> bool:
        return bool(
            db.one(
                "SELECT id FROM chat_turns WHERE id = ? AND chat_id = ? "
                "AND state = 'running' AND stop_requested = 0",
                (self.turn_id, self.chat_id),
            )
        )

    async def refresh(self) -> bool:
        if not self.active():
            return False
        # Keep a pending batch across transient/ambiguous delivery failures. Never
        # revoke it on a timeout: Docker may already have installed that batch.
        if not self.pending or min(map(expires_at, self.pending)) <= time.time() + RENEW_BEFORE:
            self.pending = await self.issue()
        if not self.active():
            return False
        if {item["full_name"] for item in self.pending} != {item["full_name"] for item in self.current}:
            raise RuntimeError("Project credential scope changed during the active turn")
        if not await broker.refresh_project_credentials(self.chat_id, self.turn_id, self.pending):
            raise RuntimeError("Agent not ready for Git credential renewal")
        self.current, self.pending = self.pending, []
        # Superseded tokens are allowed to expire naturally, so a Git process
        # already using one is not interrupted. Retain all unexpired tokens for
        # final revocation, but bound memory for turns that run for days.
        self.issued = [item for item in self.issued if expires_at(item) > time.time()]
        return True

    async def maintain(self, finished: asyncio.Event) -> None:
        if not self.current:
            return
        delay = max(0, min(map(expires_at, self.current)) - time.time() - RENEW_BEFORE)
        while not finished.is_set():
            try:
                await asyncio.wait_for(finished.wait(), delay)
                return
            except TimeoutError:
                pass
            try:
                if finished.is_set() or not await self.refresh():
                    return
                delay = max(0, min(map(expires_at, self.current)) - time.time() - RENEW_BEFORE)
            except Exception:
                # Never log token payloads or HTTP response bodies.
                logging.getLogger("nautionette").warning(
                    "Git credential renewal failed; retrying: chat=%s turn=%s",
                    self.chat_id,
                    self.turn_id,
                )
                delay = RETRY_SECONDS

    async def revoke(self) -> None:
        # Includes batches whose delivery was never acknowledged.
        unique = {item["token"]: item for item in self.issued}
        await projects.revoke_credentials(list(unique.values()))
        self.current, self.pending, self.issued = [], [], []
