"""The workflow manifest schema.

Versioned and additive: new optional keys may be added at the same schema
version, breaking changes bump `schema`. Unknown keys prefixed with `x_` are
preserved instead of rejected, so a workflow can carry data this runtime does
not understand yet.
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = 1

NAME_RE = r"^[a-z][a-z0-9_]{2,63}$"

_JSON_SCHEMA_OBJECT = {
    "type": "object",
    "properties": {
        "type": {"const": "object"},
        "properties": {"type": "object"},
        "required": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["type"],
}

MANIFEST_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Nautionette workflow manifest",
    "type": "object",
    "required": ["schema", "name", "inputs", "outputs"],
    "properties": {
        "schema": {"type": "integer", "minimum": 1, "maximum": SCHEMA_VERSION},
        "name": {"type": "string", "pattern": NAME_RE},
        "title": {"type": "string", "maxLength": 120},
        "description": {"type": "string", "maxLength": 2000},
        "version": {"type": "integer", "minimum": 1},
        "inputs": _JSON_SCHEMA_OBJECT,
        "outputs": _JSON_SCHEMA_OBJECT,
        "agent_set": {"type": "string", "pattern": r"^[a-z][a-z0-9-]{0,31}$"},
        "timeout_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
        "source": {"type": "string", "enum": ["chat", "hand-written", "git", "seed"]},
    },
    # Everything the runtime does not know must announce itself as an extension.
    "patternProperties": {"^x_": {}},
    "additionalProperties": False,
}


class ManifestError(ValueError):
    """Raised when a manifest does not satisfy the schema."""


def _validate_with_jsonschema(manifest: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a hard dependency in images
        return _validate_by_hand(manifest)

    validator = jsonschema.Draft202012Validator(MANIFEST_SCHEMA)
    return [
        f"{'/'.join(str(p) for p in error.path) or 'manifest'}: {error.message}"
        for error in sorted(validator.iter_errors(manifest), key=lambda e: list(e.path))
    ]


def _validate_by_hand(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("schema", "name", "inputs", "outputs"):
        if key not in manifest:
            errors.append(f"manifest: '{key}' is required")
    name = manifest.get("name")
    if isinstance(name, str) and not re.match(NAME_RE, name):
        errors.append("name: must be lowercase letters, digits and underscores (3-64 chars)")
    for key in ("inputs", "outputs"):
        value = manifest.get(key)
        if value is not None and (not isinstance(value, dict) or value.get("type") != "object"):
            errors.append(f'{key}: must be a JSON Schema object with "type": "object"')
    return errors


def validate_manifest(manifest: Any) -> list[str]:
    """Return a list of human readable problems. Empty means valid."""
    if not isinstance(manifest, dict):
        return ["manifest: must be a dict named MANIFEST at module level"]
    errors = _validate_with_jsonschema(manifest)
    schema = manifest.get("schema")
    if isinstance(schema, int) and schema > SCHEMA_VERSION:
        errors.append(
            f"schema: file wants manifest schema {schema}, this runtime understands {SCHEMA_VERSION}"
        )
    return errors


def normalise_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Fill in the defaults the runtime relies on, without dropping unknown keys."""
    out = dict(manifest)
    out.setdefault("schema", SCHEMA_VERSION)
    out.setdefault("version", 1)
    out.setdefault("agent_set", "default")
    out.setdefault("timeout_minutes", 30)
    out.setdefault("title", out.get("name", "").replace("_", " ").title())
    out.setdefault("description", "")
    out.setdefault("tags", [])
    out.setdefault("source", "hand-written")
    return out


def input_problems(schema: Any, payload: Any) -> list[str]:
    """Check a run's input against the manifest before Temporal ever sees it.

    A missing key would otherwise surface as a KeyError deep inside the workflow,
    which reads as a broken system rather than a missing field.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return []
    if not isinstance(payload, dict):
        return ["input must be a JSON object"]

    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema ships in every image
        missing = [key for key in schema.get("required", []) if key not in payload]
        return [f"'{key}' is required" for key in missing]

    validator = jsonschema.Draft202012Validator(schema)
    problems = []
    for error in sorted(validator.iter_errors(payload), key=lambda e: list(e.path)):
        where = "/".join(str(part) for part in error.path)
        problems.append(f"{where}: {error.message}" if where else error.message)
    return problems
