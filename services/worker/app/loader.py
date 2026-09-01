"""Load workflow files from the shared volume.

What an agent writes is what the worker loads. A broken file is skipped and
reported, never fatal: one bad draft must not take the worker down.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from nautionette import SourceError, parse_dependencies

log = logging.getLogger("worker.loader")

DEPENDENCY_TIMEOUT_SECONDS = int(os.environ.get("WORKFLOW_DEPS_TIMEOUT", "300"))


def _missing(dependencies: list[str]) -> list[str]:
    """Which of these are not importable yet, by distribution name."""
    absent = []
    for spec in dependencies:
        name = re.split(r"[\[<>=!~ ]", spec, maxsplit=1)[0]
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            absent.append(spec)
    return absent


def _install(dependencies: list[str]) -> tuple[bool, str | None]:
    """Resolve declared packages into the worker's own environment.

    Returns whether anything was newly installed, because a package that appears
    mid-process does not import reliably: the caller re-execs instead.
    """
    if not dependencies:
        return False, None
    absent = _missing(dependencies)
    if not absent:
        return False, None
    uv = shutil.which("uv")
    if not uv:
        return False, "uv is not available to install workflow dependencies"
    log.info("installing workflow dependencies: %s", ", ".join(sorted(absent)))
    try:
        completed = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
            [uv, "pip", "install", "--python", sys.executable, *sorted(absent)],
            capture_output=True,
            text=True,
            timeout=DEPENDENCY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"dependencies did not install in {DEPENDENCY_TIMEOUT_SECONDS}s"
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout or "uv pip install failed").strip()[:400]
    importlib.invalidate_caches()
    return True, None


def install_dependencies(directory: str) -> tuple[bool, dict[str, str]]:
    """Install everything the workflow files declare, before any of them is imported."""
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    wanted: set[str] = set()
    bad_headers: dict[str, str] = {}
    for file in _workflow_files(path):
        try:
            wanted.update(parse_dependencies(file.read_text(encoding="utf-8")))
        except (SourceError, OSError) as exc:
            bad_headers[file.name] = str(exc)
    installed, error = _install(sorted(wanted))
    if error:
        log.error("workflow dependencies unavailable: %s", error)
    return installed, bad_headers


def _workflow_files(path: Path) -> list[Path]:
    return [file for file in sorted(path.glob("*.py")) if not file.name.startswith("_")]


def load_workflows(
    directory: str, bad_headers: dict[str, str] | None = None
) -> tuple[list[type], list[dict[str, Any]]]:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    classes: list[type] = []
    report: list[dict[str, Any]] = []
    bad_headers = bad_headers or {}

    for file in _workflow_files(path):
        if file.name in bad_headers:
            report.append({"file": file.name, "workflows": [], "error": bad_headers[file.name]})
            log.error("skipping %s: %s", file.name, bad_headers[file.name])
            continue
        found, error = _load_file(file)
        report.append(
            {
                "file": file.name,
                "workflows": [cls.__name__ for cls in found],
                "error": error,
            }
        )
        if error:
            log.error("skipping %s: %s", file.name, error)
        else:
            log.info("loaded %s -> %s", file.name, [cls.__name__ for cls in found] or "no workflow")
        classes.extend(found)

    seen: set[str] = set()
    unique: list[type] = []
    for cls in classes:
        definition = getattr(cls, "__temporal_workflow_definition", None)
        name = getattr(definition, "name", None) or cls.__name__
        if name in seen:
            log.warning("duplicate workflow name %s, keeping the first", name)
            continue
        seen.add(name)
        unique.append(cls)
    return unique, report


def _load_file(file: Path) -> tuple[list[type], str | None]:
    module_name = f"nautionette_workflow_{file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, file)
    if spec is None or spec.loader is None:
        return [], "file could not be loaded as a module"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a bad file is data, not a crash
        sys.modules.pop(module_name, None)
        return [], f"{type(exc).__name__}: {exc}"

    found = [
        value
        for value in vars(module).values()
        if isinstance(value, type) and getattr(value, "__temporal_workflow_definition", None)
    ]
    return found, None
