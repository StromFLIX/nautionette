"""Run inside a throwaway subprocess: import a workflow file and see if it registers.

Prints one JSON object on stdout. Never trusted with anything: no network, its
own process, and it dies with the check.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"ok": False, "errors": ["checker: usage: checker.py <file> <name>"]}))
        return 2
    path, name = sys.argv[1], sys.argv[2]
    errors: list[str] = []
    info: dict[str, object] = {}

    try:
        from temporalio import workflow  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "errors": [f"temporalio unavailable: {exc}"]}))
        return 1

    module_name = f"workflow_check_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        print(json.dumps({"ok": False, "errors": ["file could not be loaded as a module"]}))
        return 1

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any import error is a validation error
        print(json.dumps({"ok": False, "errors": [f"import failed: {type(exc).__name__}: {exc}"]}))
        return 1

    defined: list[str] = []
    for attribute in vars(module).values():
        if not isinstance(attribute, type):
            continue
        definition = getattr(attribute, "__temporal_workflow_definition", None)
        if definition is not None:
            defined.append(getattr(definition, "name", None) or attribute.__name__)

    if not defined:
        errors.append("no class decorated with @workflow.defn found")
    elif name not in defined:
        errors.append(f"@workflow.defn name mismatch: file registers {defined}, expected '{name}'")
    info["registers"] = defined

    manifest = getattr(module, "MANIFEST", None)
    if not isinstance(manifest, dict):
        errors.append("MANIFEST is missing or not a dict at runtime")
    elif manifest.get("name") != name:
        errors.append(f"MANIFEST['name'] is {manifest.get('name')!r}, expected {name!r}")

    print(json.dumps({"ok": not errors, "errors": errors, "info": info}))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
