"""Model integrations: provider routes the app writes into agentgateway."""

from __future__ import annotations

from .discovery import discover_models
from .registry import INTEGRATION_TYPES, instance_label, integration_type
from .resources import configured_instances
from .service import bootstrap, ensure_defaults, payload, remove, save, summary, test

__all__ = [
    "INTEGRATION_TYPES",
    "bootstrap",
    "configured_instances",
    "discover_models",
    "ensure_defaults",
    "instance_label",
    "integration_type",
    "payload",
    "remove",
    "save",
    "summary",
    "test",
]
