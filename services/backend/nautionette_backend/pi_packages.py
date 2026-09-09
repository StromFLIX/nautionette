"""Immutable package/config revisions. Secret values never enter profile or turn JSON."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import HTTPException
from nautionette.pi_packages import configuration, filters, revision_id

from .clients import broker
from .db import db
from .events import bus
from .pi_package_secrets import seal, unseal


def installation(installation_id: str) -> dict[str, Any]:
    try:
        revision_id(installation_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = db.one("SELECT * FROM pi_package_installations WHERE id = ?", (installation_id,))
    if not row:
        raise HTTPException(404, "Package installation not found")
    return {**row, "metadata": json.loads(row["metadata"]), "allow_scripts": bool(row["allow_scripts"])}


async def finish_install(installation_id: str) -> None:
    row = installation(installation_id)
    try:
        metadata = await broker.install_package(installation_id, row["source"], row["allow_scripts"])
        db.execute(
            "UPDATE pi_package_installations SET status = 'ready', metadata = ? "
            "WHERE id = ? AND status = 'installing'",
            (json.dumps(metadata), installation_id),
        )
    except Exception:
        # Never reflect arbitrary HTTP bodies, registry output or lifecycle logs.
        db.execute(
            "UPDATE pi_package_installations SET status = 'failed', error = ? WHERE id = ?",
            (
                "Installation failed. Check image readiness, public source/version and package dependencies; "
                "retry with a new installation.",
                installation_id,
            ),
        )
    finally:
        bus.publish("agent.package.changed", {"installation_id": installation_id})


def recover_installations() -> None:
    db.execute(
        "UPDATE pi_package_installations SET status = 'failed', error = ? WHERE status = 'installing'",
        ("Installation interrupted by backend restart; retry with a new installation.",),
    )


def _revision(revision: str) -> dict[str, Any]:
    try:
        revision_id(revision)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = db.one("SELECT * FROM pi_package_revisions WHERE id = ?", (revision,))
    if not row:
        raise HTTPException(422, "Package configuration revision not found")
    return {**row, "filters": json.loads(row["filters"]), "configuration": unseal(row["configuration"])}


def public_revision(revision: str) -> dict[str, Any]:
    row = _revision(revision)
    return {
        **row,
        "configuration": {kind: dict.fromkeys(entries) for kind, entries in row["configuration"].items()},
    }


def create_revision(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.keys() - {"installation_id", "filters", "configuration", "previous_id"}:
        raise HTTPException(422, "Unknown package configuration field")
    item = installation(payload.get("installation_id", ""))
    if item["status"] != "ready":
        raise HTTPException(409, "Only successfully installed artifacts can be selected")
    previous = _revision(payload["previous_id"]) if payload.get("previous_id") else None
    try:
        selected = filters(payload.get("filters", {}))
        config = configuration(
            payload.get("configuration", {}), previous["configuration"] if previous else None
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    revision = uuid.uuid4().hex
    db.execute(
        "INSERT INTO pi_package_revisions (id, installation_id, filters, configuration, created_at) "
        "VALUES (?,?,?,?,?)",
        (revision, item["id"], json.dumps(selected), seal(config), time.time()),
    )
    return public_revision(revision)


def selection(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 20:
        raise HTTPException(422, "packages must contain at most 20 configuration revision IDs")
    seen = set()
    targets = {"env": set(), "files": set()}
    for item in value:
        try:
            revision_id(item)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        row = _revision(item)
        installed = installation(row["installation_id"])
        if installed["status"] != "ready" or row["installation_id"] in seen:
            raise HTTPException(422, "Select each ready package installation only once")
        seen.add(row["installation_id"])
        for kind, entries in row["configuration"].items():
            if targets[kind].intersection(entries):
                raise HTTPException(422, "Selected packages have conflicting configuration keys/paths")
            targets[kind].update(entries)
    return list(value)


def for_run(revisions: list[str]) -> list[dict[str, Any]]:
    selection(revisions)
    result = []
    for revision in revisions:
        row = _revision(revision)
        installed = installation(row["installation_id"])
        result.append(
            {
                "installation_id": installed["id"],
                "root": installed["metadata"]["root"],
                "filters": row["filters"],
                "configuration": row["configuration"],
            }
        )
    return result
