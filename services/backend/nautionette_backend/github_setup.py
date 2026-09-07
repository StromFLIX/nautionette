from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException

from . import projects
from .db import db
from .events import bus

BASE_PATH = "/api/projects/github-app"
COOKIE = "__Host-nautionette-github-setup"
REGISTERED_SETTING = "github_projects_registered_app"
WEBHOOK_SETTING = "github_projects_webhook"
SETUP_SECONDS = 3600
MAX_WEBHOOK_BYTES = 2 * 1024 * 1024


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def public_origin(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname or ""
        valid = (
            parsed.scheme == "https"
            and host
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and re.fullmatch(r"[A-Za-z0-9.-]+", host)
            and "." in host
            and not host.endswith((".localhost", ".local", ".internal", "."))
        )
        try:
            valid = valid and ipaddress.ip_address(host).is_global
        except ValueError:
            pass
        if not valid or (parsed.port is not None and parsed.port != 443):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(
            422, "GitHub needs a public HTTPS instance URL, such as https://nautionette.example.com"
        ) from exc
    return f"https://{host}"


def registered_app() -> dict[str, Any]:
    pending = db.get_setting(REGISTERED_SETTING, {})
    if pending:
        return pending
    current = projects.app_config()
    return current if current.get("webhook_secret") and current.get("public_url") else {}


def status() -> dict[str, Any]:
    registered = registered_app()
    return projects.app_status() | {
        "registered": bool(registered),
        "automatic": bool(projects.app_config().get("webhook_secret")),
        "public_url": registered.get("public_url", os.environ.get("GITHUB_APP_PUBLIC_URL", "")),
        "webhook": db.get_setting(WEBHOOK_SETTING, {}),
    }


def begin(public_url: str, organization: str = "") -> dict[str, str]:
    origin = public_origin(os.environ.get("GITHUB_APP_PUBLIC_URL") or public_url)
    organization = organization.strip()
    if organization and not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", organization):
        raise HTTPException(422, "Enter a GitHub organization name, not a URL")
    config = registered_app()
    if config and config.get("public_url") != origin:
        raise HTTPException(409, "Use the public URL where this App was registered to finish setup")
    state = secrets.token_urlsafe(32)
    db.execute("DELETE FROM github_app_setups WHERE expires_at < ?", (time.time(),))
    db.execute(
        "INSERT INTO github_app_setups (state_hash, phase, expires_at, public_url, organization, config) "
        "VALUES (?, 'created', ?, ?, ?, ?)",
        (digest(state), time.time() + SETUP_SECONDS, origin, organization, json.dumps(config)),
    )
    return {"start_url": f"{origin}{BASE_PATH}/start?{urlencode({'state': state})}"}


def session(state: str, browser: str, phase: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM github_app_setups WHERE state_hash = ?", (digest(state),))
    if (
        not row
        or row["expires_at"] < time.time()
        or row["phase"] != phase
        or (phase != "created" and not hmac.compare_digest(row["browser_hash"], digest(browser)))
    ):
        raise HTTPException(
            400, "GitHub setup expired or was already used. Return to Projects and connect again."
        )
    return row


def claim(row: dict[str, Any], phase: str) -> None:
    updated = db.execute(
        "UPDATE github_app_setups SET phase = ? WHERE state_hash = ? AND phase = ?",
        (phase, row["state_hash"], row["phase"]),
    ).rowcount
    if not updated:
        raise HTTPException(409, "GitHub setup is already being handled")


def installation_url(config: dict[str, Any], state: str) -> str:
    return f"https://github.com/apps/{config['slug']}/installations/new?{urlencode({'state': state})}"


def start(state: str) -> dict[str, Any]:
    row = session(state, "", "created")
    config = json.loads(row["config"])
    claim(row, "installation" if config else "manifest")
    browser = secrets.token_urlsafe(32)
    db.execute(
        "UPDATE github_app_setups SET browser_hash = ? WHERE state_hash = ?", (digest(browser), digest(state))
    )
    if config:
        return {"browser": browser, "url": installation_url(config, state)}
    origin = row["public_url"]
    owner = f"organizations/{row['organization']}/settings" if row["organization"] else "settings"
    return {
        "browser": browser,
        "url": f"https://github.com/{owner}/apps/new?{urlencode({'state': state})}",
        "manifest": {
            "name": f"Nautionette-{secrets.token_hex(4)}",
            "url": origin,
            "public": False,
            "description": "GitHub repositories for Nautionette chat workspaces",
            "redirect_url": f"{origin}{BASE_PATH}/callback",
            "setup_url": f"{origin}{BASE_PATH}/installed",
            "setup_on_update": True,
            "request_oauth_on_install": False,
            "hook_attributes": {"url": f"{origin}{BASE_PATH}/webhook", "active": True},
            "default_permissions": {"contents": "write", "metadata": "read", "workflows": "write"},
            "default_events": ["push", "repository"],
        },
    }


async def convert(state: str, browser: str, code: str) -> str:
    row = session(state, browser, "manifest")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", code):
        raise HTTPException(400, "GitHub registration was cancelled or returned no code")
    claim(row, "converting")
    result = await projects.github("POST", f"/app-manifests/{code}/conversions", "")
    if not re.fullmatch(r"[A-Za-z0-9-]+", result.get("slug", "")):
        raise HTTPException(502, "GitHub returned an invalid App registration")
    config = {
        "app_id": str(result["id"]),
        "private_key": result["pem"],
        "webhook_secret": result["webhook_secret"],
        "slug": result["slug"],
        "public_url": row["public_url"],
        "installation_id": "",
    }
    projects.app_jwt(config)
    db.set_setting(REGISTERED_SETTING, config)
    db.execute(
        "UPDATE github_app_setups SET phase = 'installation', config = ? WHERE state_hash = ?",
        (json.dumps(config), digest(state)),
    )
    return installation_url(config, state)


async def installed(state: str, browser: str, installation_id: str, action: str = "") -> str:
    row = session(state, browser, "installation")
    origin = row["public_url"]
    if action == "request":
        claim(row, "requested")
        return f"{origin}/settings/projects?github=pending"
    if not re.fullmatch(r"[0-9]{1,30}", installation_id):
        raise HTTPException(400, "GitHub did not return an installation")
    claim(row, "verifying")
    config = json.loads(row["config"])
    try:
        installation = await projects.github(
            "GET",
            f"/app/installations/{installation_id}",
            projects.app_jwt(config),
        )
        if (
            str(installation.get("app_id")) != config["app_id"]
            or installation.get("suspended_at")
            or installation.get("permissions", {}).get("contents") != "write"
        ):
            raise HTTPException(
                422, "This installation must belong to the registered App and allow Contents: read and write"
            )
    except HTTPException:
        db.execute(
            "UPDATE github_app_setups SET phase = 'installation' WHERE state_hash = ?", (digest(state),)
        )
        raise
    config.update(
        installation_id=installation_id,
        installation_status="connected",
        account=installation.get("account", {}).get("login", ""),
        workflows=installation.get("permissions", {}).get("workflows") == "write",
    )
    db.set_setting(projects.APP_SETTING, config)
    db.execute("DELETE FROM settings WHERE key = ?", (REGISTERED_SETTING,))
    projects._tokens.clear()
    db.execute("DELETE FROM github_app_setups WHERE state_hash = ?", (digest(state),))
    return f"{origin}/settings/projects?github=connected"


def webhook(body: bytes, signature: str, event: str, delivery: str) -> dict[str, Any]:
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(413, "GitHub webhook is too large")
    if not re.fullmatch(r"sha256=[a-f0-9]{64}", signature):
        raise HTTPException(401, "Invalid GitHub webhook signature")
    configs = [projects.app_config(), db.get_setting(REGISTERED_SETTING, {})]
    config = next(
        (
            candidate
            for candidate in configs
            if candidate.get("webhook_secret")
            and hmac.compare_digest(
                signature,
                "sha256=" + hmac.new(candidate["webhook_secret"].encode(), body, hashlib.sha256).hexdigest(),
            )
        ),
        None,
    )
    if not config:
        raise HTTPException(401, "Invalid GitHub webhook signature")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", delivery):
        raise HTTPException(400, "Invalid GitHub webhook delivery")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError
        installation = payload.get("installation") or {}
        repository = payload.get("repository") or {}
        if not isinstance(installation, dict) or not isinstance(repository, dict):
            raise ValueError
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, "Invalid GitHub webhook payload") from exc
    if event not in {"ping", "installation", "installation_repositories", "repository", "push"}:
        return {"ok": True, "ignored": True}
    if event != "ping" and str(installation.get("id")) != config.get("installation_id"):
        return {"ok": True, "ignored": True}
    db.execute("DELETE FROM github_webhook_deliveries WHERE received_at < ?", (time.time() - 7 * 86400,))
    if not db.execute(
        "INSERT OR IGNORE INTO github_webhook_deliveries VALUES (?,?)",
        (f"{config['app_id']}:{delivery}", time.time()),
    ).rowcount:
        return {"ok": True, "duplicate": True}
    if event in {"installation", "installation_repositories", "repository"}:
        projects._tokens.clear()
    if event == "installation":
        action = payload.get("action")
        if action in {"deleted", "suspend", "unsuspend", "new_permissions_accepted"}:
            config["installation_status"] = {"deleted": "removed", "suspend": "suspended"}.get(
                action, "connected"
            )
            permissions = installation.get("permissions") or {}
            config["workflows"] = permissions.get("workflows") == "write"
            if action in {"unsuspend", "new_permissions_accepted"} and permissions.get("contents") != "write":
                config["installation_status"] = "permissions_required"
            db.set_setting(projects.APP_SETTING, config)
    full_name = repository.get("full_name", "")
    if event in {"repository", "push"} and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
        db.execute(
            "UPDATE projects SET full_name = ?, default_branch = COALESCE(?, default_branch) "
            "WHERE repository_id = ?",
            (full_name, repository.get("default_branch"), repository.get("id")),
        )
    db.set_setting(WEBHOOK_SETTING, {"last_received_at": time.time(), "event": event})
    bus.publish(
        "projects.github_event",
        {"event": event, "action": payload.get("action", ""), "repository_id": repository.get("id")},
    )
    return {"ok": True}
