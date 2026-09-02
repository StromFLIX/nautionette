"""Asking a provider what it serves, mapped through its declaration."""

from __future__ import annotations

import re
from typing import Any

from ..clients import gateway, model_catalog
from .registry import (
    INTEGRATION_TYPES,
    integration_context,
    integration_prefix,
    integration_type,
    render,
)
from .resources import stored_config


def provider_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "other"


def _pluck(item: dict[str, Any], path: str) -> Any:
    value: Any = item
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


async def discover_models(instance: str) -> list[dict[str, Any]]:
    type_id = integration_type(instance)
    if not type_id:
        return []
    spec = INTEGRATION_TYPES[type_id]
    discovery = spec.get("discovery", {})
    config = stored_config(instance)
    context = integration_context(spec, config)
    if "url" in discovery:
        payload = await model_catalog.payload(str(render(discovery["url"], context)))
    else:
        payload = await gateway.integration_models(instance)

    mapping = discovery["models"]
    prefix = integration_prefix(spec, context)
    fallback_vendor = str(render(spec.get("vendor", spec["name"]), context))
    models: list[dict[str, Any]] = []
    for item in payload.get(mapping.get("items", "data")) or []:
        if not isinstance(item, dict):
            continue
        identifier = _pluck(item, mapping["id"])
        if not isinstance(identifier, str) or not identifier or "*" in identifier:
            continue
        include = mapping.get("include")
        if include and _pluck(item, include["path"]) not in include["values"]:
            continue
        if mapping.get("enabled") and _pluck(item, mapping["enabled"]) is False:
            continue
        vendor = _pluck(item, mapping["vendor"]) if mapping.get("vendor") else None
        label = _pluck(item, mapping["name"]) if mapping.get("name") else None
        window = next(
            (
                value
                for path in mapping.get("context", [])
                if isinstance(value := _pluck(item, path), int)
            ),
            None,
        )
        owner, separator, _ = identifier.partition("/")
        models.append(
            {
                "id": f"{prefix}/{identifier}" if prefix else identifier,
                "name": f"{vendor}: {label}" if vendor and label else (label or identifier),
                "provider": provider_slug(
                    str(vendor) if vendor else (owner if separator else fallback_vendor)
                ),
                "instance": instance,
                "context_length": window,
            }
        )
    return models
