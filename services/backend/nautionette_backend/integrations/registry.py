"""Every model integration, declared as data.

Adding a provider is an entry in `INTEGRATION_TYPES` and nothing else: the
fields the clients render, the route agentgateway is given and the way its model
list is read all come from the declaration.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

from ..fields import ENV_NAME, HTTPS_URL, SECRET, SLUG

RESOURCE_PREFIX = "nautionette-integration-"
LEGACY_COPILOT_PREFIX = "nautionette-copilot-"
INITIALIZED_SETTING = "model_integrations_initialized_v2"
CONFIG_SETTING = "model_integration:"
INSTANCE_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")

# How to read a provider's model list. Paths are dotted lookups into each entry.
_OPENAI_MODELS = {"items": "data", "id": "id", "name": "name"}

INTEGRATION_TYPES: dict[str, dict[str, Any]] = {
    "openrouter": {
        "name": "OpenRouter",
        "description": "A unified catalog of hosted models from many vendors.",
        "provider": "openrouter",
        "prefix": "",
        "auth": {"kind": "key", "env": "OPENROUTER_API_KEY"},
        "discovery": {
            "url": "https://openrouter.ai/api/v1/models",
            "models": {**_OPENAI_MODELS, "context": ["context_length"]},
        },
        "default": True,
    },
    "copilot": {
        "name": "GitHub Copilot",
        "description": "Models the GitHub account behind this Copilot token may use.",
        "provider": "copilot",
        "prefix": "copilot",
        "auth": {
            "kind": "key",
            "env": "GH_COPILOT_TOKEN",
            "builtin": "copilot",
            "label": "GitHub token",
            "placeholder": "gho_…",
        },
        # agentgateway sends these itself only when it supplies the token; a typed one needs them here.
        "headers": {
            "Copilot-Integration-Id": "{integration_id}",
            "editor-version": "agentgateway/0.0.0",
        },
        "discovery": {
            "host": "api.githubcopilot.com:443",
            "path": "/models",
            "models": {
                **_OPENAI_MODELS,
                "vendor": "vendor",
                "enabled": "model_picker_enabled",
                "include": {"path": "capabilities.type", "values": ["chat"]},
                "context": [
                    "capabilities.limits.max_context_window_tokens",
                    "capabilities.limits.max_prompt_tokens",
                ],
            },
        },
        "fields": [
            {
                "key": "integration_id",
                "label": "Copilot integration ID",
                "default": "copilot-developer-cli",
                "pattern": r"[a-z0-9][a-z0-9._-]{0,63}",
                "help": "Keep the official CLI default unless GitHub assigned this app another ID.",
            }
        ],
    },
    "openai": {
        "name": "OpenAI",
        "description": "OpenAI's own API, billed to your OpenAI account.",
        "provider": "openAI",
        "prefix": "openai",
        "auth": {"kind": "key", "env": "OPENAI_API_KEY"},
        "discovery": {"host": "api.openai.com:443", "path": "/v1/models", "models": _OPENAI_MODELS},
    },
    "anthropic": {
        "name": "Anthropic",
        "description": "Claude models straight from Anthropic.",
        "provider": "anthropic",
        "prefix": "anthropic",
        "auth": {
            "kind": "key",
            "env": "ANTHROPIC_API_KEY",
            "location": {"header": {"name": "x-api-key"}},
        },
        "discovery": {
            "host": "api.anthropic.com:443",
            "path": "/v1/models",
            "headers": {"anthropic-version": "2023-06-01"},
            "models": {**_OPENAI_MODELS, "name": "display_name"},
        },
    },
    "groq": {
        "name": "Groq",
        "description": "Open models on Groq's low-latency inference stack.",
        "provider": "groq",
        "prefix": "groq",
        "auth": {"kind": "key", "env": "GROQ_API_KEY"},
        "discovery": {
            "host": "api.groq.com:443",
            "path": "/openai/v1/models",
            "models": {**_OPENAI_MODELS, "vendor": "owned_by", "context": ["context_window"]},
        },
    },
    "mistral": {
        "name": "Mistral",
        "description": "Mistral's hosted models.",
        "provider": "mistral",
        "prefix": "mistral",
        "auth": {"kind": "key", "env": "MISTRAL_API_KEY"},
        "discovery": {
            "host": "api.mistral.ai:443",
            "path": "/v1/models",
            "models": {**_OPENAI_MODELS, "context": ["max_context_length"]},
        },
    },
    "deepseek": {
        "name": "DeepSeek",
        "description": "DeepSeek's hosted models.",
        "provider": "deepseek",
        "prefix": "deepseek",
        "auth": {"kind": "key", "env": "DEEPSEEK_API_KEY"},
        "discovery": {"host": "api.deepseek.com:443", "path": "/models", "models": _OPENAI_MODELS},
    },
    "xai": {
        "name": "xAI",
        "description": "Grok models from xAI.",
        "provider": "xai",
        "prefix": "xai",
        "auth": {"kind": "key", "env": "XAI_API_KEY"},
        "discovery": {"host": "api.x.ai:443", "path": "/v1/models", "models": _OPENAI_MODELS},
    },
    "custom": {
        "name": "Custom",
        "description": "Any other endpoint that speaks the OpenAI chat completions API.",
        "provider": {"custom": {"formats": [{"type": "completions"}]}},
        "prefix": "{slug}",
        "vendor": "{slug}",
        "instance": "custom-{slug}",
        "multiple": True,
        "params": {"baseUrl": "{base_url}"},
        "auth": {"kind": "key"},
        "discovery": {
            "host": "{base_url_host}",
            "path": "{base_url_path}/models",
            "models": _OPENAI_MODELS,
        },
        "fields": [
            {
                "key": "slug",
                "label": "Name",
                "pattern": SLUG,
                "placeholder": "my-provider",
                "help": "Also the model prefix, so its models appear as my-provider/<model>.",
            },
            {
                "key": "base_url",
                "label": "Base URL",
                "kind": "url",
                "public": True,
                "pattern": HTTPS_URL,
                "placeholder": "https://api.example.com/v1",
                "help": "The OpenAI-compatible base, without the /chat/completions suffix.",
            },
        ],
    },
}


def integration_type(instance: str) -> str | None:
    """Which type an instance belongs to, e.g. custom-mylab -> custom."""
    spec = INTEGRATION_TYPES.get(instance)
    if spec and not spec.get("multiple"):
        return instance
    return next(
        (
            type_id
            for type_id, candidate in INTEGRATION_TYPES.items()
            if candidate.get("multiple") and instance.startswith(f"{type_id}-")
        ),
        None,
    )


def integration_spec(type_id: str) -> dict[str, Any]:
    spec = INTEGRATION_TYPES.get(type_id)
    if not spec:
        raise HTTPException(status_code=404, detail="unknown model integration")
    return spec


def integration_fields(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Declared fields, plus the API key every key-authenticated provider needs."""
    fields = list(spec.get("fields", []))
    if spec.get("auth", {}).get("kind") != "key":
        return fields
    variable = spec["auth"].get("env", "")
    fallback = (
        f"Leave it empty to fall back to ${variable} on the agentgateway service."
        if variable
        else "Leave it empty if the endpoint needs no key."
    )
    return [
        *fields,
        {
            "key": "api_key",
            "label": str(spec["auth"].get("label", "API key")),
            "kind": "secret",
            "optional": True,
            "pattern": SECRET,
            "placeholder": str(spec["auth"].get("placeholder", "sk-…")),
            "help": f"Paste the key and agentgateway keeps it; it is never shown again. {fallback}",
        },
    ]


def render(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format_map(context) if "{" in value else value
    if isinstance(value, dict):
        return {key: render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, context) for item in value]
    return value


def integration_context(spec: dict[str, Any], config: dict[str, str]) -> dict[str, str]:
    """The values a declaration may interpolate, derived from what was typed in."""
    context: dict[str, str] = {}
    for field in spec.get("fields", []):
        value = str(config.get(field["key"]) or field.get("default", "")).strip()
        context[field["key"]] = value
        if field.get("kind") == "url" and value:
            parts = urlsplit(value)
            context[f"{field['key']}_host"] = f"{parts.hostname}:{parts.port or 443}"
            context[f"{field['key']}_path"] = parts.path.rstrip("/")
    return context


def integration_prefix(spec: dict[str, Any], context: dict[str, str]) -> str:
    return str(render(spec.get("prefix", ""), context))


def instance_label(instance: str) -> str:
    type_id = integration_type(instance)
    if not type_id:
        return instance
    spec = INTEGRATION_TYPES[type_id]
    if not spec.get("multiple"):
        return str(spec["name"])
    return f"{spec['name']}: {instance.removeprefix(f'{type_id}-')}"


__all__ = [
    "CONFIG_SETTING",
    "ENV_NAME",
    "INITIALIZED_SETTING",
    "INSTANCE_PATTERN",
    "INTEGRATION_TYPES",
    "LEGACY_COPILOT_PREFIX",
    "RESOURCE_PREFIX",
    "integration_context",
    "integration_fields",
    "integration_prefix",
    "integration_spec",
    "integration_type",
    "instance_label",
    "render",
]
