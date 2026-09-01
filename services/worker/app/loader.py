"""Load workflow files from the shared volume.

What an agent writes is what the worker loads. A broken file is skipped and
reported, never fatal: one bad draft must not take the worker down.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("worker.loader")


def load_workflows(directory: str) -> tuple[list[type], list[dict[str, Any]]]:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    classes: list[type] = []
    report: list[dict[str, Any]] = []

    for file in sorted(path.glob("*.py")):
        if file.name.startswith("_"):
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
