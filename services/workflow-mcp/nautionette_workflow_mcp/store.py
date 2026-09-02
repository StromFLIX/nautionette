"""Files on the shared volume. Live workflows next to their drafts."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from nautionette.manifest import normalise_manifest, validate_manifest
from nautionette.source import SourceError, find_workflow_classes, parse_manifest, unified_diff

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

WORKFLOWS_DIR = Path(os.environ.get("WORKFLOWS_DIR", "/workflows"))
DRAFTS_DIR = WORKFLOWS_DIR / ".drafts"


class StoreError(ValueError):
    pass


def ensure_dirs() -> None:
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)


def check_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise StoreError("name must be lowercase letters, digits and underscores, 3-64 characters")
    return name


def workflow_path(name: str) -> Path:
    return WORKFLOWS_DIR / f"{check_name(name)}.py"


def draft_path(name: str) -> Path:
    return DRAFTS_DIR / f"{check_name(name)}.py"


def draft_meta_path(name: str) -> Path:
    return DRAFTS_DIR / f"{check_name(name)}.json"


def describe(path: Path, name: str) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    entry: dict[str, Any] = {
        "name": name,
        "path": str(path),
        "bytes": len(source.encode("utf-8")),
        "updated_at": path.stat().st_mtime,
    }
    try:
        manifest = normalise_manifest(parse_manifest(source))
        entry["manifest"] = manifest
        entry["title"] = manifest.get("title") or name
        entry["description"] = manifest.get("description", "")
        entry["problems"] = validate_manifest(manifest)
        entry["classes"] = find_workflow_classes(source)
    except SourceError as exc:
        entry["manifest"] = None
        entry["title"] = name
        entry["description"] = ""
        entry["problems"] = [str(exc)]
        entry["classes"] = []
    return entry


def list_workflows() -> list[dict[str, Any]]:
    ensure_dirs()
    out = []
    for path in sorted(WORKFLOWS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        out.append(describe(path, path.stem))
    return out


def read_workflow(name: str) -> dict[str, Any]:
    path = workflow_path(name)
    if not path.is_file():
        raise StoreError(f"workflow '{name}' does not exist")
    entry = describe(path, name)
    entry["code"] = path.read_text(encoding="utf-8")
    return entry


def list_drafts() -> list[dict[str, Any]]:
    ensure_dirs()
    out = []
    for path in sorted(DRAFTS_DIR.glob("*.py")):
        entry = describe(path, path.stem)
        meta = draft_meta_path(path.stem)
        entry["meta"] = json.loads(meta.read_text()) if meta.is_file() else {}
        out.append(entry)
    return out


def read_draft(name: str) -> dict[str, Any]:
    path = draft_path(name)
    if not path.is_file():
        raise StoreError(f"draft '{name}' does not exist")
    entry = describe(path, name)
    entry["code"] = path.read_text(encoding="utf-8")
    meta = draft_meta_path(name)
    entry["meta"] = json.loads(meta.read_text()) if meta.is_file() else {}
    live = workflow_path(name)
    entry["diff"] = unified_diff(
        live.read_text(encoding="utf-8") if live.is_file() else "",
        entry["code"],
        f"{name}.py",
    )
    entry["is_new"] = not live.is_file()
    return entry


def write_draft(name: str, code: str, message: str = "") -> dict[str, Any]:
    ensure_dirs()
    name = check_name(name)
    path = draft_path(name)
    path.write_text(code, encoding="utf-8")
    draft_meta_path(name).write_text(
        json.dumps({"message": message, "written_at": time.time()}, indent=2), encoding="utf-8"
    )
    return read_draft(name)


def publish_draft(name: str) -> dict[str, Any]:
    draft = draft_path(name)
    if not draft.is_file():
        raise StoreError(f"draft '{name}' does not exist")
    code = draft.read_text(encoding="utf-8")
    target = workflow_path(name)
    previous = target.read_text(encoding="utf-8") if target.is_file() else ""
    target.write_text(code, encoding="utf-8")
    draft.unlink(missing_ok=True)
    draft_meta_path(name).unlink(missing_ok=True)
    return {
        "name": name,
        "published": True,
        "diff": unified_diff(previous, code, f"{name}.py"),
        "path": str(target),
    }


def discard_draft(name: str) -> dict[str, Any]:
    draft_path(name).unlink(missing_ok=True)
    draft_meta_path(name).unlink(missing_ok=True)
    return {"name": name, "discarded": True}


def delete_workflow(name: str) -> dict[str, Any]:
    path = workflow_path(name)
    if not path.is_file():
        raise StoreError(f"workflow '{name}' does not exist")
    path.unlink()
    return {"name": name, "deleted": True}
