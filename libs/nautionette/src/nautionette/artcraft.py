"""The fixed ArtCraft catalog and release/mount contracts (no arbitrary code sources)."""

from __future__ import annotations

import json
import platform
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

TOOL_NAMES = frozenset({"craft_apps", "craft_tools", "craft_call"})
_VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-[a-zA-Z0-9][a-zA-Z0-9.-]{0,40})?")


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    # Docker services get the same file the Pi image uses. Editable workspace
    # installs read it directly; never fetch defaults from the internet at boot.
    path = Path("/etc/nautionette/artcraft-catalog.json")
    if not path.exists():
        path = Path(__file__).resolve().parents[4] / "images/pi-base/artcraft/catalog.json"
    return json.loads(path.read_text())


def app_id(value: Any) -> str:
    if not isinstance(value, str) or value not in catalog():
        raise ValueError("Unknown ArtCraft app")
    return value


def version(value: Any) -> str:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError("Use an exact release version, for example 0.8.0 (not latest, a URL or a branch)")
    return value


def architecture() -> str:
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine(), platform.machine())
    if arch not in {"x86_64", "aarch64"}:
        raise ValueError("ArtCraft requires Linux x86_64 or aarch64")
    return arch


def release(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"app", "version", "arch", "sha256"}:
        raise ValueError("Invalid ArtCraft release specification")
    app_id(value["app"])
    version(value["version"])
    if value["arch"] != architecture():
        raise ValueError("ArtCraft release architecture does not match this server")
    if not isinstance(value["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"]):
        raise ValueError("ArtCraft releases require a SHA-256 digest")
    return dict(value)


def installation_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid ArtCraft installation ID")
    return value


def asset_name(app: str, selected_version: str, arch: str) -> str:
    suffix = "-cli" if catalog()[app].get("cli_archive") else ""
    return f"{app}{suffix}-{selected_version}-linux-{arch}.tar.gz"


def bundled_release(app: str) -> dict[str, str]:
    item = catalog()[app_id(app)]
    arch = architecture()
    return {"app": app, "version": item["version"], "arch": arch, "sha256": item["sha256"][arch]}


def runtime_selection(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > len(catalog()):
        raise ValueError("Invalid ArtCraft runtime selection")
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) - {"app", "release", "installation_id"}:
            raise ValueError("Invalid ArtCraft runtime entry")
        app = app_id(item.get("app"))
        if app in seen:
            raise ValueError("Duplicate ArtCraft runtime entry")
        seen.add(app)
        spec = release(item.get("release"))
        if spec["app"] != app:
            raise ValueError("ArtCraft app/release mismatch")
        if "installation_id" in item:
            installation_id(item["installation_id"])
        elif spec != bundled_release(app):
            raise ValueError("Unbundled ArtCraft releases require a verified installation")
    return value
