"""Shared definitions used by the worker, the authoring server and the backend."""

from .manifest import (
    MANIFEST_SCHEMA,
    SCHEMA_VERSION,
    ManifestError,
    input_problems,
    normalise_manifest,
    validate_manifest,
)

__all__ = [
    "MANIFEST_SCHEMA",
    "SCHEMA_VERSION",
    "ManifestError",
    "input_problems",
    "normalise_manifest",
    "validate_manifest",
    "ACTIVITIES",
]

# Activity names every worker registers. Workflow files call them by string, so a
# workflow file never has to import anything from the runtime.
ACTIVITIES = (
    "agent_call",
    "http_fetch",
    "emit_event",
    "save_artifact",
    "read_artifact",
)
