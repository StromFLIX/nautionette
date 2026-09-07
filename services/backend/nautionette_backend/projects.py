from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import jwt
from fastapi import HTTPException

from .background import spawn
from .clients.http import shared
from .db import db

PROJECTS_DIR = Path(os.environ.get("PROJECTS_DIR", "/projects"))
APP_SETTING = "github_projects_app"
_tokens: dict[int, tuple[str, float]] = {}


def app_config() -> dict[str, Any]:
    return db.get_setting(APP_SETTING, {})


def app_status() -> dict[str, Any]:
    config = app_config()
    return {key: config.get(key, "") for key in ("app_id", "installation_id", "slug")} | {
        "configured": bool(config.get("private_key") and config.get("installation_id"))
        and config.get("installation_status", "connected") == "connected",
        "installation_status": config.get("installation_status", ""),
        "account": config.get("account", ""),
        "install_url": f"https://github.com/apps/{config['slug']}/installations/new"
        if config.get("slug")
        else "",
    }


def app_jwt(config: dict[str, Any]) -> str:
    try:
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": config["app_id"]},
            config["private_key"],
            algorithm="RS256",
        )
    except (KeyError, ValueError, TypeError, jwt.PyJWTError) as exc:
        raise HTTPException(422, "A valid GitHub App ID and RSA private key are required") from exc


async def github(method: str, path: str, token: str, **kwargs) -> dict[str, Any]:
    try:
        response = await shared().request(
            method,
            "https://api.github.com" + path,
            headers={
                **({"Authorization": f"Bearer {token}"} if token else {}),
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
            **kwargs,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, "GitHub is unreachable; try again") from exc
    if not response.is_success:
        raise HTTPException(
            502,
            f"GitHub returned {response.status_code}; check the App installation and repository permissions",
        )
    return response.json() if response.content else {}


async def configure(app_id: str, installation_id: str, private_key: str) -> dict[str, Any]:
    config = {
        "app_id": app_id,
        "installation_id": installation_id,
        "private_key": private_key.strip() or app_config().get("private_key", ""),
    }
    token = app_jwt(config)
    app = await github("GET", "/app", token)
    installation = await github("GET", f"/app/installations/{installation_id}", token)
    if installation.get("permissions", {}).get("contents") != "write":
        raise HTTPException(422, "The GitHub App installation needs Contents: read and write")
    config["slug"] = app["slug"]
    config["workflows"] = installation.get("permissions", {}).get("workflows") == "write"
    db.set_setting(APP_SETTING, config)
    _tokens.clear()
    return app_status()


async def installation_token(repository_id: int | None = None) -> str:
    cached = _tokens.get(repository_id or 0)
    if cached and cached[1] > time.time():
        return cached[0]
    result = await installation_access(repository_id)
    _tokens[repository_id or 0] = (result["token"], time.time() + 3000)
    return result["token"]


async def installation_access(repository_id: int | None = None) -> dict[str, Any]:
    config = app_config()
    if not config.get("installation_id") or config.get("installation_status", "connected") != "connected":
        raise HTTPException(409, "Connect a GitHub App in Projects settings first")
    permissions = {"contents": "write"}
    if config.get("workflows"):
        permissions["workflows"] = "write"
    payload: dict[str, Any] = {"permissions": permissions}
    if repository_id is not None:
        payload["repository_ids"] = [repository_id]
    return await github(
        "POST", f"/app/installations/{config['installation_id']}/access_tokens", app_jwt(config), json=payload
    )


async def repositories(page: int) -> dict[str, Any]:
    result = await github(
        "GET", f"/installation/repositories?per_page=50&page={page}", await installation_token()
    )
    return {
        "total_count": result["total_count"],
        "repositories": [
            {key: repository.get(key) for key in ("id", "full_name", "private", "default_branch")}
            for repository in result["repositories"]
        ],
    }


def checkout(project_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", project_id):
        raise HTTPException(422, "Invalid project ID")
    path = PROJECTS_DIR / project_id
    if path.is_symlink():
        raise HTTPException(409, "Project checkout cannot be a symlink")
    return path


def list_projects() -> list[dict[str, Any]]:
    return [
        row | {"path": f"/projects/{row['id']}"}
        for row in db.query("SELECT * FROM projects WHERE status != 'archived' ORDER BY full_name")
    ]


def selection(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 20 or any(not isinstance(item, str) for item in value):
        raise HTTPException(422, "project_ids must be a list of at most 20 project IDs")
    selected = list(dict.fromkeys(value))
    for project_id in selected:
        project = db.one("SELECT status FROM projects WHERE id = ?", (project_id,))
        if not project or project["status"] != "ready" or not checkout(project_id).is_dir():
            raise HTTPException(422, "A selected project is no longer ready; update the project selection")
    return selected


async def add_repository(full_name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
        raise HTTPException(422, "Repository must be owner/name")
    repository = await github("GET", f"/repos/{full_name}", await installation_token())
    await installation_token(repository["id"])
    existing = db.one("SELECT * FROM projects WHERE repository_id = ?", (repository["id"],))
    if existing:
        if existing["status"] == "archived":
            if not checkout(existing["id"]).is_dir():
                raise HTTPException(409, "The saved checkout is missing")
            db.execute("UPDATE projects SET status = 'ready' WHERE id = ?", (existing["id"],))
        elif existing["status"] == "failed":
            db.execute("UPDATE projects SET status = 'cloning', error = '' WHERE id = ?", (existing["id"],))
            spawn(clone(existing), name=f"clone-{existing['id']}")
        return db.one("SELECT * FROM projects WHERE id = ?", (existing["id"],)) or existing
    project = {
        "id": uuid.uuid4().hex,
        "repository_id": repository["id"],
        "full_name": repository["full_name"],
        "default_branch": repository["default_branch"],
        "status": "cloning",
        "error": "",
    }
    db.execute(
        "INSERT INTO projects (id, repository_id, full_name, default_branch, status) VALUES (?,?,?,?,?)",
        (project["id"], project["repository_id"], project["full_name"], project["default_branch"], "cloning"),
    )
    spawn(clone(project), name=f"clone-{project['id']}")
    return project


async def git(*args: str, env: dict[str, str] | None = None) -> None:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(600):
            code = await process.wait()
        if code:
            raise RuntimeError("Git operation failed; verify repository access and available disk space")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def clone(project: dict[str, Any]) -> None:
    destination = checkout(project["id"])
    staging = PROJECTS_DIR / (project["id"] + ".clone")
    try:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise RuntimeError("A checkout already exists; it has been left untouched")
        if staging.exists():
            shutil.rmtree(staging)
        token = await installation_token(project["repository_id"])
        credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        environment = dict(
            os.environ,
            GIT_TERMINAL_PROMPT="0",
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
            GIT_CONFIG_VALUE_0=f"Authorization: Basic {credentials}",
        )
        await git(
            "clone", "--", f"https://github.com/{project['full_name']}.git", str(staging), env=environment
        )
        staging.rename(destination)
        db.execute("UPDATE projects SET status = 'ready', error = '' WHERE id = ?", (project["id"],))
    except asyncio.CancelledError:
        db.execute(
            "UPDATE projects SET status = 'failed', error = 'Download interrupted; retry' WHERE id = ?",
            (project["id"],),
        )
        raise
    except Exception:
        db.execute(
            "UPDATE projects SET status = 'failed', error = ? WHERE id = ?",
            ("Download failed; check GitHub App access and disk space, then retry", project["id"]),
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def archive(project_id: str) -> None:
    project = db.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not project:
        raise HTTPException(404, "Project not found")
    if project["status"] == "cloning" or db.one(
        "SELECT * FROM project_leases WHERE project_id = ?", (project_id,)
    ):
        raise HTTPException(409, "Project is in use; try again when it is idle")
    db.execute("UPDATE projects SET status = 'archived' WHERE id = ?", (project_id,))


async def agent_credentials(project_ids: list[str]) -> list[dict[str, Any]]:
    credentials = []
    try:
        for project_id in selection(project_ids):
            project = db.one("SELECT * FROM projects WHERE id = ?", (project_id,))
            access = await installation_access(project["repository_id"])
            credentials.append(
                {
                    "full_name": project["full_name"],
                    "token": access["token"],
                    "expires_at": access["expires_at"],
                }
            )
        return credentials
    except BaseException:
        await revoke_credentials(credentials)
        raise


async def revoke_credentials(credentials: list[dict[str, Any]]) -> None:
    for credential in credentials:
        try:
            await github("DELETE", "/installation/token", credential["token"])
        except HTTPException:
            pass


def prepare_worktrees(chat_id: str, project_ids: list[str]) -> dict[str, Any]:
    if not project_ids:
        return {}
    if not re.fullmatch(r"[a-f0-9]{12}", chat_id):
        raise ValueError("Invalid chat ID")
    baselines = {}
    remotes = {}
    for project_id in selection(project_ids):
        directory = PROJECTS_DIR / ".sessions" / project_id / chat_id
        if any(parent.is_symlink() for parent in (directory, directory.parent, directory.parent.parent)):
            raise ValueError("Project worktrees cannot be symlinks")
        directory.mkdir(parents=True, exist_ok=True)
        project = db.one("SELECT default_branch, full_name FROM projects WHERE id = ?", (project_id,))
        baselines[project_id] = project["default_branch"]
        remotes[project_id] = project["full_name"]
    return {"project_baselines": baselines, "project_remotes": remotes}
