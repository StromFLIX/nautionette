"""Authenticated package catalog and installation/configuration management."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from nautionette.pi_packages import installation_source

from .. import pi_packages
from ..background import spawn
from ..clients.http import shared
from ..db import db
from ..security import require_user

router = APIRouter(dependencies=[Depends(require_user)])
_search_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}


@router.get("/api/pi-packages/search")
async def search_packages(
    q: str = Query(default="", max_length=120), offset: int = Query(default=0, ge=0, le=1000)
):
    key = (q.strip(), offset)
    cached = _search_cache.get(key)
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    try:
        response = await shared().get(
            "https://registry.npmjs.org/-/v1/search",
            params={"text": f"keywords:pi-package {key[0]}", "size": 20, "from": offset},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        items = []
        for item in data.get("objects", []):
            package = item.get("package", {})
            if "pi-package" not in package.get("keywords", []):
                continue
            try:
                installation_source(f"npm:{package['name']}@{package['version']}")
            except (KeyError, ValueError):
                continue
            items.append(
                {
                    "name": package["name"],
                    "version": package["version"],
                    "description": str(package.get("description", ""))[:500],
                }
            )
        result = {
            "packages": items,
            "next_offset": offset + 20 if offset + 20 < data.get("total", 0) else None,
        }
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(502, "npm package search is unavailable; retry or install by source") from exc
    if len(_search_cache) >= 100:
        _search_cache.clear()
    _search_cache[key] = (time.monotonic(), result)
    return result


@router.get("/api/pi-packages/installations")
async def list_installations():
    return {
        "installations": [
            pi_packages.library_installation(row["id"])
            for row in db.query("SELECT id FROM pi_package_installations ORDER BY created_at DESC")
        ]
    }


@router.post("/api/pi-packages/installations", status_code=202)
async def install_package(payload: dict[str, Any] = Body(...)):
    if payload.keys() - {"source", "allow_scripts"} or type(payload.get("allow_scripts", False)) is not bool:
        raise HTTPException(422, "Provide source and optional boolean allow_scripts")
    try:
        installation_source(payload.get("source"))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if db.one("SELECT COUNT(*) AS n FROM pi_package_installations WHERE status = 'installing'")["n"] >= 2:
        raise HTTPException(409, "Two package installations are already running; retry shortly")
    installation_id = uuid.uuid4().hex
    db.execute(
        "INSERT INTO pi_package_installations (id, source, allow_scripts, status, created_at) "
        "VALUES (?,?,?,?,?)",
        (
            installation_id,
            payload["source"].strip(),
            int(payload.get("allow_scripts", False)),
            "installing",
            time.time(),
        ),
    )
    spawn(pi_packages.finish_install(installation_id), name=f"package-{installation_id}")
    return pi_packages.installation(installation_id)


@router.patch("/api/pi-packages/installations/{installation_id}/configuration")
async def configure_installation(installation_id: str, payload: dict[str, Any] = Body(...)):
    return pi_packages.configure_library(installation_id, payload)


@router.get("/api/pi-packages/revisions/{revision_id}")
async def get_revision(revision_id: str):
    return pi_packages.public_revision(revision_id)


@router.post("/api/pi-packages/revisions", status_code=201)
async def create_revision(payload: dict[str, Any] = Body(...)):
    return pi_packages.create_revision(payload)
