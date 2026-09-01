"""The check chain every write goes through, in order.

1. tool arguments against the tool schema  (done by the caller / MCP layer)
2. manifest against the workflow schema
3. the file parses, imports in a throwaway subprocess and registers with Temporal
4. a diff for a human

Extensible by design: a new rule is a new step in `run_checks`.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from nautionette.manifest import normalise_manifest, validate_manifest
from nautionette.source import SourceError, find_workflow_classes, parse_manifest

CHECKER = str(Path(__file__).with_name("checker.py"))
IMPORT_TIMEOUT_SECONDS = 30
MAX_SOURCE_BYTES = 256_000


def run_checks(name: str, code: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    manifest: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = []

    def step(label: str, ok: bool, detail: str = "") -> None:
        steps.append({"step": label, "ok": ok, "detail": detail})

    if not code.strip():
        return {
            "valid": False,
            "errors": ["file is empty"],
            "warnings": [],
            "manifest": None,
            "steps": [{"step": "source", "ok": False, "detail": "file is empty"}],
        }
    if len(code.encode("utf-8")) > MAX_SOURCE_BYTES:
        return {
            "valid": False,
            "errors": [f"file is larger than {MAX_SOURCE_BYTES} bytes"],
            "warnings": [],
            "manifest": None,
            "steps": [{"step": "source", "ok": False, "detail": "too large"}],
        }

    # 2. manifest
    try:
        manifest = parse_manifest(code)
        problems = validate_manifest(manifest)
        if manifest.get("name") != name:
            problems.append(f"MANIFEST['name'] is {manifest.get('name')!r}, expected {name!r}")
        errors.extend(problems)
        step("manifest", not problems, "; ".join(problems))
        manifest = normalise_manifest(manifest)
    except SourceError as exc:
        errors.append(str(exc))
        step("manifest", False, str(exc))
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "manifest": None,
            "steps": steps,
        }

    # 3a. a workflow class exists (cheap check before spending a subprocess)
    classes = find_workflow_classes(code)
    if not classes:
        errors.append("no class decorated with @workflow.defn found")
    step("workflow_class", bool(classes), ", ".join(classes))

    # 3b. it actually imports and registers
    import_result = _import_check(name, code)
    errors.extend(import_result["errors"])
    step("import", import_result["ok"], "; ".join(import_result["errors"]) or "imports cleanly")

    if "agent_call" not in code and "execute_activity" not in code:
        warnings.append("workflow calls no activities; it will do nothing on its own")

    return {
        "valid": not errors,
        "errors": list(dict.fromkeys(errors)),
        "warnings": warnings,
        "manifest": manifest,
        "classes": classes,
        "steps": steps,
    }


def _import_check(name: str, code: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="wfcheck-") as tmp:
        path = Path(tmp) / f"{name}.py"
        path.write_text(code, encoding="utf-8")
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [sys.executable, CHECKER, str(path), name],
                capture_output=True,
                text=True,
                timeout=IMPORT_TIMEOUT_SECONDS,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "errors": [f"import did not finish in {IMPORT_TIMEOUT_SECONDS}s"]}

        stdout = (completed.stdout or "").strip().splitlines()
        if stdout:
            import json

            try:
                payload = json.loads(stdout[-1])
                return {"ok": bool(payload.get("ok")), "errors": list(payload.get("errors", []))}
            except ValueError:
                pass
        detail = (completed.stderr or completed.stdout or "no output").strip()[-500:]
        return {"ok": False, "errors": [f"import check failed: {detail}"]}
