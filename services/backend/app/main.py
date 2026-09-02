"""The backend: the only service that publishes a port.

Auth, chats, workflows, triggers and the stream back to the clients all live
here. It never touches the Docker socket; the broker does that.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import json
import os
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from nautionette import input_problems

from .agent import (
    DEFAULT_HISTORY_CHARS,
    agent_job,
    build_history,
    call_agent,
    promote_chat,
    stream_agent,
    summarise_for_title,
)
from .clients import authoring, broker, gateway, model_catalog, upstream_problem
from .config import settings
from .db import Database
from .events import bus, sse
from .temporal_gateway import temporal


def seed_workflows() -> None:
    """Copy committed workflows into the shared volume on first start."""
    source, target = Path(settings.seed_dir), Path(settings.workflows_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / ".drafts").mkdir(exist_ok=True)
    if not source.exists():
        return
    for item in source.glob("*.py"):
        destination = target / item.name
        if not destination.exists():
            shutil.copy2(item, destination)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(settings.artifacts_dir).mkdir(parents=True, exist_ok=True)
    seed_workflows()
    integration_bootstrap = asyncio.create_task(_bootstrap_default_integrations())
    bus.publish("system.start", {"version": settings.version})
    yield
    integration_bootstrap.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await integration_bootstrap


app = FastAPI(
    title="Nautionette",
    version=settings.version,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# The packaged app runs on its own webview origin, so it is cross-origin to this
# API. Credentials stay off: every call carries a bearer token, never a cookie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
db = Database(os.path.join(settings.data_dir, "nautionette.db"))

# Flipped by the first agent call that comes back clean, so the status page can
# say "a model answered" without anyone configuring a second flag.
_agent_has_answered = False


# ----------------------------------------------------------------------- auth


def _token_matches(supplied: str | None, expected: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied or "", expected)


async def require_user(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    x_auth_token: str | None = Header(default=None),
) -> None:
    if not settings.auth_enabled:
        return
    supplied = None
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    supplied = supplied or x_auth_token or token
    if not _token_matches(supplied, settings.app_token):
        raise HTTPException(status_code=401, detail="unauthorized")


async def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    if not settings.internal_token:
        return
    if not _token_matches(x_internal_token, settings.internal_token):
        raise HTTPException(status_code=401, detail="unauthorized")


def _remember_agent_result(ok: bool) -> None:
    global _agent_has_answered
    if ok:
        _agent_has_answered = True


# ------------------------------------------------------------------- settings

# Rough but stable: a token is about four characters of English.
CHARS_PER_TOKEN = 4
# Half the window for transcript, leaving room for the system prompt, the new
# turn, tool schemas and the answer itself.
HISTORY_SHARE = 0.5

# Context length per model id, refreshed whenever the catalog is built.
_model_windows: dict[str, int] = {}


# The environment sets the floor; anything saved in the app wins over it.
def _defaults() -> dict[str, Any]:
    return {
        "default_model": settings.agent_model,
        "default_agent_set": settings.default_agent_set,
        # 0 means "work it out from the model", which is what you want by default.
        "history_chars": 0,
    }


def runtime(key: str) -> Any:
    return db.get_setting(key, _defaults()[key])


def history_budget(model: str | None) -> int:
    """How much transcript this model can actually be handed."""
    override = int(runtime("history_chars") or 0)
    if override > 0:
        return override
    window = _model_windows.get(model or runtime("default_model"))
    if window:
        return int(window * CHARS_PER_TOKEN * HISTORY_SHARE)
    return DEFAULT_HISTORY_CHARS


@app.get("/api/settings", dependencies=[Depends(require_user)])
async def get_settings() -> dict[str, Any]:
    return {"settings": {key: runtime(key) for key in _defaults()}, "defaults": _defaults()}


@app.put("/api/settings", dependencies=[Depends(require_user)])
async def put_settings(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    for key in _defaults():
        if key not in payload:
            continue
        value = payload[key]
        if value in (None, ""):
            db.execute("DELETE FROM settings WHERE key = ?", (key,))
        elif key == "history_chars":
            chars = int(value)
            db.set_setting(key, 0 if chars <= 0 else max(2_000, min(2_000_000, chars)))
        else:
            db.set_setting(key, str(value))
    _catalog_cache.update(at=0.0, value=None)
    bus.publish("settings.changed", {})
    return {"settings": {key: runtime(key) for key in _defaults()}, "defaults": _defaults()}


# --------------------------------------------------------------------- health


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"status": "ok", "version": settings.version}


@app.get("/api/system", dependencies=[Depends(require_user)])
async def system_status() -> dict[str, Any]:
    async def safe(coro, name: str) -> dict[str, Any]:
        try:
            return {"name": name, "status": "ok", "detail": await coro}
        except Exception as exc:  # noqa: BLE001 - status page must never 500
            return {"name": name, "status": "down", "detail": str(exc)[:200]}

    temporal_ok, broker_state, gateway_state, authoring_state = await asyncio.gather(
        temporal.healthy(),
        safe(broker.health(), "broker"),
        safe(gateway.health(), "agentgateway"),
        safe(authoring.health(), "workflow-mcp"),
    )
    agent_sets: list[dict[str, Any]] = []
    if broker_state["status"] == "ok":
        try:
            agent_sets = await broker.agent_sets()
        except Exception:  # noqa: BLE001
            agent_sets = []
    return {
        "version": settings.version,
        "auth_enabled": settings.auth_enabled,
        "model": settings.agent_model,
        "model_key_present": settings.model_key_present or _agent_has_answered,
        "components": [
            {
                "name": "temporal",
                "status": "ok" if temporal_ok else "down",
                "detail": temporal.last_error or settings.temporal_address,
            },
            broker_state,
            gateway_state,
            authoring_state,
        ],
        "agent_sets": agent_sets,
    }


@app.get("/api/events", dependencies=[Depends(require_user)])
async def events_stream() -> StreamingResponse:
    return StreamingResponse(
        bus.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/events/recent", dependencies=[Depends(require_user)])
async def events_recent() -> dict[str, Any]:
    return {"events": bus.history()[-100:]}


# -------------------------------------------------------------------- catalog

_catalog_cache: dict[str, Any] = {"at": 0.0, "value": None}
_CATALOG_TTL = 60.0
_INTEGRATION_RESOURCE_PREFIX = "nautionette-integration-"
_LEGACY_COPILOT_RESOURCE_PREFIX = "nautionette-copilot-"
_INTEGRATIONS_INITIALIZED = "model_integrations_initialized_v2"
_INSTANCE_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_integration_lock = asyncio.Lock()

# Field values are display state; agentgateway stays the source of truth for what is configured.
_INTEGRATION_SETTING = "model_integration:"

_SLUG = r"[a-z0-9][a-z0-9-]{0,23}"
_ENV_NAME = r"[A-Z][A-Z0-9_]{0,63}"
_HTTPS_URL = r"https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/-]*)?"

# How to read a provider's model list. Paths are dotted lookups into each entry.
_OPENAI_MODELS = {"items": "data", "id": "id", "name": "name"}

# Every integration is data: adding a provider here needs no new code.
_INTEGRATION_TYPES: dict[str, dict[str, Any]] = {
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
                "pattern": _SLUG,
                "placeholder": "my-provider",
                "help": "Also the model prefix, so its models appear as my-provider/<model>.",
            },
            {
                "key": "base_url",
                "label": "Base URL",
                "kind": "url",
                "public": True,
                "pattern": _HTTPS_URL,
                "placeholder": "https://api.example.com/v1",
                "help": "The OpenAI-compatible base, without the /chat/completions suffix.",
            },
        ],
    },
}


def _integration_fields(spec: dict[str, Any]) -> list[dict[str, Any]]:
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
            "pattern": rf"(?:\${_ENV_NAME}|\S(?:.*\S)?)",
            "placeholder": str(spec["auth"].get("placeholder", "sk-…")),
            "help": (
                f"Paste the key and agentgateway keeps it; it is never shown again. {fallback}"
            ),
        },
    ]


def _integration_resource_id(instance: str) -> str:
    return f"{_INTEGRATION_RESOURCE_PREFIX}{instance}"


def _integration_discovery_resource_id(instance: str) -> str:
    return f"{_integration_resource_id(instance)}-discovery"


def _integration_type(instance: str) -> str | None:
    """Resolve which integration type an instance belongs to, e.g. custom-mylab -> custom."""
    spec = _INTEGRATION_TYPES.get(instance)
    if spec and not spec.get("multiple"):
        return instance
    return next(
        (
            type_id
            for type_id, candidate in _INTEGRATION_TYPES.items()
            if candidate.get("multiple") and instance.startswith(f"{type_id}-")
        ),
        None,
    )


def _integration_spec(type_id: str) -> dict[str, Any]:
    spec = _INTEGRATION_TYPES.get(type_id)
    if not spec:
        raise HTTPException(status_code=404, detail="unknown model integration")
    return spec


def _render(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format_map(context) if "{" in value else value
    if isinstance(value, dict):
        return {key: _render(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item, context) for item in value]
    return value


def _integration_context(spec: dict[str, Any], config: dict[str, str]) -> dict[str, str]:
    context: dict[str, str] = {}
    for field in spec.get("fields", []):
        value = str(config.get(field["key"]) or field.get("default", "")).strip()
        context[field["key"]] = value
        if field.get("kind") == "url" and value:
            parts = urlsplit(value)
            context[f"{field['key']}_host"] = f"{parts.hostname}:{parts.port or 443}"
            context[f"{field['key']}_path"] = parts.path.rstrip("/")
    return context


def _credential(spec: dict[str, Any], supplied: str, existing: str) -> str:
    """A typed key wins, then whatever the gateway already holds, then the declared variable."""
    auth = spec.get("auth", {})
    if auth.get("kind") != "key":
        return ""
    if supplied:
        return supplied
    if existing:
        return existing
    if auth.get("builtin"):
        return ""  # sending nothing lets agentgateway fall back to its own credential
    variable = auth.get("env", "")
    return f"${variable}" if variable else ""


def _credential_state(spec: dict[str, Any], credential: str) -> dict[str, str]:
    if credential.startswith("$"):
        return {"mode": "environment", "variable": credential[1:]}
    if credential:
        return {"mode": "stored", "variable": ""}
    auth = spec.get("auth", {})
    if auth.get("builtin"):
        return {"mode": "gateway", "variable": str(auth.get("env", ""))}
    return {"mode": "none", "variable": ""}


def _credential_hint(spec: dict[str, Any], credential: str) -> str:
    state = _credential_state(spec, credential)
    if state["mode"] == "stored":
        return "the stored API key"
    return state["variable"] or "a key"


def _reachable_host(url: str) -> None:
    """A user-supplied endpoint must be public: the gateway can also see this network."""
    host = urlsplit(url).hostname or ""
    with contextlib.suppress(ValueError):
        address = ipaddress.ip_address(host)
        if not address.is_global:
            raise HTTPException(status_code=400, detail="the base URL must be a public address")
    if "." not in host or host.endswith((".local", ".internal")):
        raise HTTPException(status_code=400, detail="the base URL must be a public hostname")


def _normalise_integration_config(spec: dict[str, Any], payload: dict[str, Any]) -> dict[str, str]:
    config: dict[str, str] = {}
    for field in _integration_fields(spec):
        value = str(payload.get(field["key"]) or field.get("default", "")).strip()
        if not value and field.get("optional"):
            config[field["key"]] = ""
            continue
        if not re.fullmatch(field["pattern"], value):
            raise HTTPException(status_code=400, detail=f"invalid value for {field['label']}")
        if field.get("public"):
            _reachable_host(value)
        config[field["key"]] = value
    return config


def _integration_prefix(spec: dict[str, Any], context: dict[str, str]) -> str:
    return str(_render(spec.get("prefix", ""), context))


def _integration_model_value(
    spec: dict[str, Any], instance: str, config: dict[str, str], credential: str = ""
) -> dict[str, Any]:
    context = _integration_context(spec, config)
    prefix = _integration_prefix(spec, context)
    value: dict[str, Any] = {
        "id": _integration_resource_id(instance),
        "name": f"{prefix}/*" if prefix else "*",
        "provider": _render(spec["provider"], context),
    }
    params = dict(_render(spec.get("params", {}), context))
    if credential:
        params["apiKey"] = credential
    if params:
        value["params"] = params
    if prefix:
        value["transformation"] = {"model": f'llmRequest.model.stripPrefix("{prefix}/")'}
    headers = _render(spec.get("headers", {}), context)
    if headers:
        value["requestHeaders"] = {"set": headers}
    return value


def _integration_discovery_value(
    spec: dict[str, Any], instance: str, config: dict[str, str], credential: str = ""
) -> dict[str, Any] | None:
    """The route that lets the backend read a provider's own model list, credential included."""
    discovery = spec.get("discovery", {})
    if "host" not in discovery:
        return None
    context = _integration_context(spec, config)
    policies: dict[str, Any] = {
        "urlRewrite": {"path": {"full": _render(discovery["path"], context)}},
        "backendTLS": {},
    }
    headers = _render({**spec.get("headers", {}), **discovery.get("headers", {})}, context)
    if headers:
        policies["requestHeaderModifier"] = {"set": headers}
    backend: dict[str, Any] = {"host": _render(discovery["host"], context)}
    auth = spec.get("auth", {})
    if credential:
        key: dict[str, Any] = {"value": credential}
        if auth.get("location"):
            key["location"] = auth["location"]
        backend["policies"] = {"backendAuth": {"key": key}}
    elif auth.get("builtin"):
        backend["policies"] = {"backendAuth": auth["builtin"]}
    return {
        "name": _integration_discovery_resource_id(instance),
        "gateways": ["default"],
        "matches": [
            {"path": {"exact": f"/_nautionette/integrations/{instance}/models"}, "method": "GET"}
        ],
        "policies": policies,
        "backends": [backend],
    }


def _existing_credential(instance: str, models: list[dict[str, Any]]) -> str:
    """The key agentgateway already holds, so reconfiguring never needs it retyped."""
    resource = _resource_map(models).get(_integration_resource_id(instance), {})
    return str((resource.get("value", {}).get("params") or {}).get("apiKey") or "")


def _stored_config(instance: str) -> dict[str, str]:
    stored = db.get_setting(f"{_INTEGRATION_SETTING}{instance}", {})
    return stored if isinstance(stored, dict) else {}


def _configured_instances(config: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for route in config.get("model_routes") or []:
        identifier = route.get("id") or ""
        if identifier.startswith(_INTEGRATION_RESOURCE_PREFIX):
            instance = identifier.removeprefix(_INTEGRATION_RESOURCE_PREFIX)
            if _integration_type(instance):
                out.append(instance)
    return out


def _provider_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "other"


def _pluck(item: dict[str, Any], path: str) -> Any:
    value: Any = item
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _route_for_model(model_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
    """The route agentgateway itself would pick, so labels match where a call really goes."""
    winner: dict[str, Any] | None = None
    winner_score = -1
    for route in config.get("model_routes") or []:
        pattern = route.get("name", "")
        if not pattern or not route.get("provider"):
            continue
        if pattern == model_id:
            score = 10_000 + len(pattern)
        elif pattern.endswith("*") and model_id.startswith(pattern[:-1]):
            score = len(pattern) - 1
        else:
            continue
        if score > winner_score:
            winner, winner_score = route, score
    return winner


def _instance_label(instance: str) -> str:
    type_id = _integration_type(instance)
    if not type_id:
        return instance
    spec = _INTEGRATION_TYPES[type_id]
    if not spec.get("multiple"):
        return str(spec["name"])
    return f"{spec['name']}: {instance.removeprefix(f'{type_id}-')}"


async def _attempt(coro, fallback):
    try:
        return await coro
    except Exception:  # noqa: BLE001 - a catalog that fails is an empty picker
        return fallback


async def _discover_integration_models(instance: str) -> list[dict[str, Any]]:
    """Ask a provider what it serves, mapped through the declaration in the registry."""
    type_id = _integration_type(instance)
    if not type_id:
        return []
    spec = _INTEGRATION_TYPES[type_id]
    discovery = spec.get("discovery", {})
    config = _stored_config(instance)
    context = _integration_context(spec, config)
    if "url" in discovery:
        payload = await model_catalog.payload(str(_render(discovery["url"], context)))
    else:
        payload = await gateway.integration_models(instance)

    mapping = discovery["models"]
    prefix = _integration_prefix(spec, context)
    fallback_vendor = str(_render(spec.get("vendor", spec["name"]), context))
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
                "provider": _provider_slug(
                    str(vendor) if vendor else (owner if separator else fallback_vendor)
                ),
                "instance": instance,
                "context_length": window,
            }
        )
    return models


async def _model_catalog(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Models the gateway will serve, tagged with who owns them and who fronts them."""
    listed = await _attempt(gateway.models(), [])
    instances = _configured_instances(config)
    discoveries = await asyncio.gather(
        *(_attempt(_discover_integration_models(instance), []) for instance in instances)
    )
    merged: dict[str, dict[str, Any]] = {}
    for model in [*({"id": item["id"]} for item in listed), *(m for d in discoveries for m in d)]:
        merged.setdefault(model["id"], {}).update(model)

    out = []
    for model in merged.values():
        # A leading "~" marks an always-latest alias, not a separate vendor.
        alias = model["id"].startswith("~")
        owner, separator, _ = model["id"].lstrip("~").partition("/")
        route = _route_for_model(model["id"], config) or {}
        identifier = str(route.get("id") or "")
        serving = (
            identifier.removeprefix(_INTEGRATION_RESOURCE_PREFIX)
            if identifier.startswith(_INTEGRATION_RESOURCE_PREFIX)
            else ""
        )
        out.append(
            {
                "id": model["id"],
                "name": model.get("name") or model["id"],
                "provider": model.get("provider") or (owner if separator else "other"),
                # Attribution follows the winning route, so the label matches where calls go.
                "gateway": _instance_label(serving) if serving else str(route.get("provider") or "gateway"),
                "integration": serving or None,
                "context_length": model.get("context_length"),
                "alias": alias,
            }
        )
    return sorted(out, key=lambda model: (model["gateway"], model["provider"], model["id"]))


async def _tool_catalog(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Federated tools, each attributed to the MCP server it came from."""
    targets = config.get("targets") or []
    federated = await _attempt(gateway.mcp_tools(), [])

    def prefixed(name: str) -> str:
        """agentgateway names a federated tool after the target it came from."""
        return next(
            (
                target["name"]
                for target in targets
                if any(name.startswith(f"{target['name']}{sep}") for sep in ("_", "-", ":"))
            ),
            "",
        )

    owner_of: dict[str, str] = {}
    # Asking every server directly is the fallback for a gateway that does not
    # prefix, and a remote server is slow, so only pay for it when a name needs it.
    if any(not prefixed(tool["name"]) for tool in federated):
        per_target = await asyncio.gather(
            *(_attempt(gateway.mcp_tools(target["host"]), []) for target in targets)
        )
        for target, tools in zip(targets, per_target, strict=True):
            for tool in tools:
                owner_of[tool["name"]] = target["name"]

    def server_for(name: str) -> str:
        return owner_of.get(name) or prefixed(name) or "other"

    tools = [{**tool, "server": server_for(tool["name"])} for tool in federated]
    servers = [
        {
            "name": target["name"],
            "host": target["host"],
            "count": sum(1 for tool in tools if tool["server"] == target["name"]),
        }
        for target in targets
    ]
    if any(tool["server"] == "other" for tool in tools):
        servers.append(
            {
                "name": "other",
                "host": "",
                "count": sum(1 for tool in tools if tool["server"] == "other"),
            }
        )
    return tools, servers


@app.get("/api/catalog", dependencies=[Depends(require_user)])
async def catalog(refresh: bool = False) -> dict[str, Any]:
    """What a chat can be pointed at: agent sets, models, MCP tools."""
    if not refresh and _catalog_cache["value"] and time.time() - _catalog_cache["at"] < _CATALOG_TTL:
        return _catalog_cache["value"]

    with contextlib.suppress(HTTPException):
        await _ensure_default_integrations()
    config = await _attempt(gateway.config(), {"providers": [], "targets": [], "wildcard_models": False})
    agent_sets, models, (tools, servers) = await asyncio.gather(
        _attempt(broker.agent_sets(), []),
        _model_catalog(config),
        _tool_catalog(config),
    )
    value = {
        "agent_sets": agent_sets or [{"name": settings.default_agent_set, "ready": True}],
        "default_agent_set": runtime("default_agent_set"),
        "models": models,
        "default_model": runtime("default_model"),
        "gateways": sorted({model["gateway"] for model in models}),
        "tools": tools,
        "tool_servers": servers,
        # The clients work the budget out per model with the same arithmetic.
        "context": {
            "chars_per_token": CHARS_PER_TOKEN,
            "history_share": HISTORY_SHARE,
            "override": int(runtime("history_chars") or 0),
            "fallback": DEFAULT_HISTORY_CHARS,
        },
    }
    _model_windows.clear()
    _model_windows.update(
        {model["id"]: model["context_length"] for model in models if model.get("context_length")}
    )
    _catalog_cache.update(at=time.time(), value=value)
    return value


# --------------------------------------------------------- model integrations


def _resource_map(resources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        resource["id"]: resource
        for resource in resources
        if isinstance(resource.get("id"), str)
        and isinstance(resource.get("value"), dict)
    }


def _gateway_problem(exc: httpx.HTTPError, credential: str = "") -> HTTPException:
    response_status = 502
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text
        missing = re.search(r"key '([A-Z0-9_]+)' up: environment variable not found", body)
        # agentgateway refuses a credential it cannot read, and refuses an empty one outright.
        if missing or (credential.startswith("$") and "BackendAuthCompat" in body):
            variable = missing.group(1) if missing else credential[1:]
            return HTTPException(
                status_code=400,
                detail=(
                    f"agentgateway has no value for {variable}. Enter the key here "
                    "instead, or set that variable on the agentgateway service."
                ),
            )
        if status == 409:
            response_status = 409
            detail = "agentgateway rejected a conflicting integration resource"
        else:
            detail = f"agentgateway configuration request failed (HTTP {status})"
    else:
        detail = "agentgateway is unavailable"
    return HTTPException(status_code=response_status, detail=detail)


def _discovery_failure(spec: dict[str, Any], credential: str, exc: Exception) -> dict[str, Any]:
    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    body = exc.response.text if isinstance(exc, httpx.HTTPStatusError) else ""
    return {
        "ok": False,
        "status": status,
        "message": upstream_problem(
            str(spec["name"]), _credential_hint(spec, credential), status, body
        ),
    }


async def _gateway_integration_resources() -> tuple[
    str, list[dict[str, Any]], list[dict[str, Any]]
]:
    try:
        runtime_state, models, routes = await asyncio.gather(
            gateway.runtime(),
            gateway.config_resources("llm.model"),
            gateway.config_resources("traffic.route"),
        )
    except httpx.HTTPError as exc:
        raise _gateway_problem(exc) from exc
    mode = (runtime_state.get("ui") or {}).get("configStoreMode", "unknown")
    return mode, models, routes


def _writable_gateway(mode: str) -> None:
    if mode != "hybrid":
        raise HTTPException(
            status_code=409,
            detail=f"agentgateway configuration storage is {mode!r}; hybrid mode is required",
        )


async def _ensure_default_integrations() -> None:
    if db.get_setting(_INTEGRATIONS_INITIALIZED, False):
        return
    async with _integration_lock:
        if db.get_setting(_INTEGRATIONS_INITIALIZED, False):
            return
        mode, models, routes = await _gateway_integration_resources()
        if mode != "hybrid":
            return
        try:
            model_resources = _resource_map(models)
            for type_id, spec in _INTEGRATION_TYPES.items():
                if not spec.get("default"):
                    continue
                value = _integration_model_value(spec, type_id, {})
                if model_resources.get(value["id"], {}).get("value") != value:
                    await gateway.put_config_resources("llm.model", [value])

            # Copilot was once configured one model at a time; fold those into its integration.
            legacy = [
                resource["id"]
                for resource in models
                if isinstance(resource.get("id"), str)
                and resource["id"].startswith(_LEGACY_COPILOT_RESOURCE_PREFIX)
            ]
            if legacy:
                await _write_integration("copilot", "copilot", {}, models, routes)
                for resource_id in legacy:
                    await gateway.delete_config_resource("llm.model", resource_id)
        except httpx.HTTPError as exc:
            raise _gateway_problem(exc) from exc
        db.set_setting(_INTEGRATIONS_INITIALIZED, True)
        _catalog_cache.update(at=0.0, value=None)


async def _bootstrap_default_integrations() -> None:
    for attempt in range(8):
        try:
            await _ensure_default_integrations()
            if db.get_setting(_INTEGRATIONS_INITIALIZED, False):
                return
        except (HTTPException, httpx.HTTPError):
            pass
        await asyncio.sleep(min(0.25 * 2**attempt, 5.0))


async def _integration_summary(
    instance: str, configured: bool, credential: str = ""
) -> dict[str, Any]:
    type_id = _integration_type(instance) or instance
    spec = _INTEGRATION_TYPES[type_id]
    config = _stored_config(instance) if configured else {}
    discovery: dict[str, Any] = {
        "ok": False,
        "status": None,
        "message": "Add this integration to discover its models.",
    }
    models: list[dict[str, Any]] = []
    if configured:
        try:
            models = await _discover_integration_models(instance)
            discovery = {"ok": True, "status": 200, "message": f"Discovered {len(models)} models."}
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            discovery = _discovery_failure(spec, credential, exc)
    context = _integration_context(spec, config)
    prefix = _integration_prefix(spec, context)
    return {
        "instance": instance,
        "type": type_id,
        "name": _instance_label(instance) if configured else str(spec["name"]),
        "description": str(spec["description"]),
        "configured": configured,
        "multiple": bool(spec.get("multiple")),
        "model_count": len(models),
        "model_match": f"{prefix}/*" if prefix else "*",
        "credential": _credential_state(spec, credential),
        "fields": _integration_fields(spec),
        "config": config,
        "discovery": discovery,
    }


async def _model_integrations_payload() -> dict[str, Any]:
    mode, models, _ = await _gateway_integration_resources()
    instances = [
        resource_id.removeprefix(_INTEGRATION_RESOURCE_PREFIX)
        for resource_id in _resource_map(models)
        if resource_id.startswith(_INTEGRATION_RESOURCE_PREFIX)
    ]
    instances = sorted(instance for instance in instances if _integration_type(instance))
    configured = await asyncio.gather(
        *(
            _integration_summary(instance, True, _existing_credential(instance, models))
            for instance in instances
        )
    )
    taken = {summary["type"] for summary in configured}
    available = await asyncio.gather(
        *(
            _integration_summary(type_id, False)
            for type_id, spec in _INTEGRATION_TYPES.items()
            if spec.get("multiple") or type_id not in taken
        )
    )
    return {
        "integrations": list(configured),
        "available": list(available),
        "storage_mode": mode,
        "writable": mode == "hybrid",
    }


async def _upsert_resource_if_changed(
    kind: str,
    value: dict[str, Any],
    resources: list[dict[str, Any]],
) -> None:
    resource_id = value.get("id") or value.get("name")
    current = _resource_map(resources).get(resource_id, {})
    if current.get("value") != value:
        await gateway.put_config_resources(kind, [value])


async def _write_integration(
    type_id: str,
    instance: str,
    config: dict[str, str],
    models: list[dict[str, Any]],
    routes: list[dict[str, Any]],
) -> None:
    spec = _INTEGRATION_TYPES[type_id]
    credential = _credential(spec, config.pop("api_key", ""), _existing_credential(instance, models))
    await _upsert_resource_if_changed(
        "llm.model", _integration_model_value(spec, instance, config, credential), models
    )
    discovery = _integration_discovery_value(spec, instance, config, credential)
    if discovery:
        await _upsert_resource_if_changed("traffic.route", discovery, routes)
    # The key stays with agentgateway; only the visible fields are kept here.
    db.set_setting(f"{_INTEGRATION_SETTING}{instance}", config)


async def _reset_default_to_available_model(excluded: str | None = None) -> bool:
    config = await _attempt(
        gateway.config(), {"providers": [], "targets": [], "model_routes": []}
    )
    models = [
        model for model in await _model_catalog(config) if model.get("integration") != excluded
    ]
    ids = {model["id"] for model in models}
    current = str(runtime("default_model") or "")
    if current in ids:
        return False
    replacement = settings.agent_model if settings.agent_model in ids else None
    replacement = replacement or (models[0]["id"] if models else None)
    if replacement:
        db.set_setting("default_model", replacement)
    else:
        db.execute("DELETE FROM settings WHERE key = ?", ("default_model",))
    bus.publish("settings.changed", {})
    return True


@app.get("/api/model-integrations", dependencies=[Depends(require_user)])
async def get_model_integrations() -> dict[str, Any]:
    await _ensure_default_integrations()
    return await _model_integrations_payload()


@app.put("/api/model-integrations/{target}", dependencies=[Depends(require_user)])
async def put_model_integration(
    target: str, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    """`target` is a type when adding, or an existing instance when reconfiguring."""
    type_id = _integration_type(target) or target
    spec = _integration_spec(type_id)
    config = _normalise_integration_config(spec, payload)
    instance = str(_render(spec.get("instance", type_id), _integration_context(spec, config)))
    if not _INSTANCE_PATTERN.fullmatch(instance) or _integration_type(instance) != type_id:
        raise HTTPException(status_code=400, detail="invalid integration name")

    await _ensure_default_integrations()
    mode, models, routes = await _gateway_integration_resources()
    _writable_gateway(mode)
    try:
        await _write_integration(type_id, instance, config, models, routes)
    except httpx.HTTPError as exc:
        if _integration_resource_id(instance) not in _resource_map(models):
            # Never leave a route behind that the discovery half could not be written for.
            with contextlib.suppress(httpx.HTTPError):
                await gateway.delete_config_resource(
                    "llm.model", _integration_resource_id(instance)
                )
        credential = _credential(
            spec, str(payload.get("api_key") or ""), _existing_credential(instance, models)
        )
        raise _gateway_problem(exc, credential) from exc

    db.set_setting(_INTEGRATIONS_INITIALIZED, True)
    _catalog_cache.update(at=0.0, value=None)
    bus.publish("model.integration.changed", {"integration": instance, "configured": True})
    return await _model_integrations_payload()


@app.delete("/api/model-integrations/{instance}", dependencies=[Depends(require_user)])
async def delete_model_integration(instance: str) -> dict[str, Any]:
    if not _integration_type(instance):
        raise HTTPException(status_code=404, detail="unknown model integration")
    mode, models, routes = await _gateway_integration_resources()
    _writable_gateway(mode)
    try:
        if _integration_resource_id(instance) in _resource_map(models):
            await gateway.delete_config_resource("llm.model", _integration_resource_id(instance))
        if _integration_discovery_resource_id(instance) in _resource_map(routes):
            await gateway.delete_config_resource(
                "traffic.route", _integration_discovery_resource_id(instance)
            )
    except httpx.HTTPError as exc:
        raise _gateway_problem(exc) from exc

    db.execute("DELETE FROM settings WHERE key = ?", (f"{_INTEGRATION_SETTING}{instance}",))
    _catalog_cache.update(at=0.0, value=None)
    default_reset = await _reset_default_to_available_model(instance)
    bus.publish("model.integration.changed", {"integration": instance, "configured": False})
    return {
        **await _model_integrations_payload(),
        "default_model": runtime("default_model"),
        "default_reset": default_reset,
    }


@app.post("/api/model-integrations/{instance}/test", dependencies=[Depends(require_user)])
async def test_model_integration(instance: str) -> dict[str, Any]:
    type_id = _integration_type(instance)
    if not type_id:
        raise HTTPException(status_code=404, detail="unknown model integration")
    spec = _INTEGRATION_TYPES[type_id]
    _, models, _ = await _gateway_integration_resources()
    if _integration_resource_id(instance) not in _resource_map(models):
        raise HTTPException(status_code=409, detail=f"add {spec['name']} first")
    credential = _existing_credential(instance, models)
    try:
        discovered = await _discover_integration_models(instance)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return _discovery_failure(spec, credential, exc)
    if not discovered:
        return {"ok": False, "status": None, "message": "No chat models were discovered."}

    default_model = str(runtime("default_model") or "")
    model = next(
        (item for item in discovered if item["id"] == default_model),
        discovered[0],
    )
    try:
        result = await gateway.test_model(
            model["id"], str(spec["name"]), _credential_hint(spec, credential)
        )
    except httpx.HTTPError as exc:
        raise _gateway_problem(exc) from exc
    _remember_agent_result(bool(result["ok"]))
    bus.publish("model.integration.test", {"integration": instance, "ok": result["ok"]})
    return result


# ------------------------------------------------------------------ mcp servers

_MCP_URL = r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[^\s?#]*)?(?:\?[^\s#]*)?"

# A target's name is also the prefix agentgateway puts on every tool it federates.
_MCP_SERVER_FIELDS: list[dict[str, Any]] = [
    {
        "key": "name",
        "label": "Name",
        "pattern": _SLUG,
        "placeholder": "linear",
        "help": "Also the tool prefix, so its tools arrive as linear_<tool>.",
        "hint": "Lower-case letters, digits and dashes, up to 24 characters.",
    },
    {
        "key": "url",
        "label": "Endpoint URL",
        "kind": "url",
        "pattern": _MCP_URL,
        "placeholder": "https://mcp.example.com/mcp",
        "help": "The streamable HTTP endpoint, exactly as an MCP client would be given it.",
        "hint": "An http:// or https:// URL. A query string is fine; spaces are not.",
    },
    {
        "key": "token",
        "label": "Access token",
        "kind": "secret",
        "optional": True,
        "pattern": rf"(?:\${_ENV_NAME}|\S(?:.*\S)?)",
        "placeholder": "sent as Authorization: Bearer",
        "help": (
            "agentgateway keeps it and it is never shown again. Leave it empty for an open "
            "server, or name a variable set on agentgateway as $MY_TOKEN."
        ),
        "hint": "The token itself, or $MY_TOKEN in capitals to name a variable.",
    },
]


def _mcp_endpoint(url: str) -> None:
    """The gateway shares this network, so a target must not point back at its own door."""
    host = (urlsplit(url).hostname or "").lower()
    with contextlib.suppress(ValueError):
        address = ipaddress.ip_address(host)
        if address.is_loopback or address.is_link_local or address.is_unspecified:
            raise HTTPException(status_code=400, detail="that address is not a reachable server")
    if host in {"localhost", (urlsplit(settings.gateway_url).hostname or "").lower()}:
        raise HTTPException(status_code=400, detail="that address is agentgateway itself")


def _mcp_target_value(name: str, url: str, credential: str) -> dict[str, Any]:
    value: dict[str, Any] = {"name": name, "mcp": {"host": url}}
    if credential:
        value["policies"] = {"backendAuth": {"key": {"value": credential}}}
    return value


def _mcp_credential(name: str, targets: list[dict[str, Any]]) -> str:
    """The token agentgateway already holds, so changing a URL never needs it retyped."""
    policies = (_resource_map(targets).get(name, {}).get("value", {}).get("policies")) or {}
    key = (policies.get("backendAuth") or {}).get("key")
    return str((key.get("value") if isinstance(key, dict) else key) or "")


async def _mcp_resources() -> tuple[str, list[dict[str, Any]]]:
    try:
        runtime_state, targets = await asyncio.gather(
            gateway.runtime(), gateway.config_resources("mcp.target")
        )
    except httpx.HTTPError as exc:
        raise _gateway_problem(exc) from exc
    return (runtime_state.get("ui") or {}).get("configStoreMode", "unknown"), targets


async def _mcp_probe(url: str, credential: str) -> dict[str, Any]:
    """A handshake before anything is written: one bad target takes /mcp down with it."""
    held_by_gateway = credential.startswith("$")
    extra = {"Authorization": f"Bearer {credential}"} if credential and not held_by_gateway else {}
    try:
        tools = await gateway.mcp_tools(url, extra)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403} and held_by_gateway:
            # Only agentgateway can read a key it holds, so a refusal here is no verdict.
            return {"ok": True, "status": status, "message": "The server asked for its token."}
        return {"ok": False, "status": status, "message": f"The server answered HTTP {status}."}
    except httpx.HTTPError:
        return {"ok": False, "status": None, "message": "The server could not be reached."}
    except ValueError:
        return {"ok": False, "status": None, "message": "That endpoint does not speak MCP."}
    return {"ok": True, "status": 200, "message": f"Answered with {len(tools)} tools."}


async def _mcp_servers_payload() -> dict[str, Any]:
    mode, targets = await _mcp_resources()
    config = await _attempt(gateway.config(), {"targets": []})
    _, servers = await _tool_catalog(config)
    counts = {server["name"]: server["count"] for server in servers}
    managed = _resource_map(targets)
    return {
        "servers": [
            {
                "name": target["name"],
                "url": target["host"],
                # A file-owned target is the checked-in baseline; the app cannot touch it.
                "managed": target["name"] in managed,
                "credential": _credential_state({}, _mcp_credential(target["name"], targets)),
                "tool_count": counts.get(target["name"], 0),
            }
            for target in config.get("targets") or []
        ],
        "fields": _MCP_SERVER_FIELDS,
        "storage_mode": mode,
        "writable": mode == "hybrid",
    }


@app.get("/api/mcp-servers", dependencies=[Depends(require_user)])
async def get_mcp_servers() -> dict[str, Any]:
    return await _mcp_servers_payload()


@app.put("/api/mcp-servers/{name}", dependencies=[Depends(require_user)])
async def put_mcp_server(name: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """The path names the server; a name is never renamed, because tools are named after it."""
    config = _normalise_integration_config({"fields": _MCP_SERVER_FIELDS}, {**payload, "name": name})
    url = config["url"]
    _mcp_endpoint(url)

    mode, targets = await _mcp_resources()
    _writable_gateway(mode)
    managed = _resource_map(targets)
    if name not in managed:
        baseline = await _attempt(gateway.config(), {"targets": []})
        if any(target["name"] == name for target in baseline.get("targets") or []):
            raise HTTPException(status_code=409, detail=f"{name} is defined in the gateway config")

    credential = config["token"] or _mcp_credential(name, targets)
    probe = await _mcp_probe(url, credential)
    if not probe["ok"]:
        raise HTTPException(status_code=400, detail=probe["message"])
    try:
        await gateway.put_config_resources("mcp.target", [_mcp_target_value(name, url, credential)])
    except httpx.HTTPError as exc:
        raise _gateway_problem(exc, credential) from exc

    _catalog_cache.update(at=0.0, value=None)
    bus.publish("mcp.server.changed", {"server": name, "configured": True})
    return await _mcp_servers_payload()


@app.delete("/api/mcp-servers/{name}", dependencies=[Depends(require_user)])
async def delete_mcp_server(name: str) -> dict[str, Any]:
    mode, targets = await _mcp_resources()
    _writable_gateway(mode)
    if name not in _resource_map(targets):
        raise HTTPException(status_code=404, detail="unknown MCP server")
    try:
        await gateway.delete_config_resource("mcp.target", name)
    except httpx.HTTPError as exc:
        raise _gateway_problem(exc) from exc

    _catalog_cache.update(at=0.0, value=None)
    bus.publish("mcp.server.changed", {"server": name, "configured": False})
    return await _mcp_servers_payload()


@app.post("/api/mcp-servers/{name}/test", dependencies=[Depends(require_user)])
async def test_mcp_server(name: str) -> dict[str, Any]:
    _, targets = await _mcp_resources()
    target = _resource_map(targets).get(name, {}).get("value")
    if not target:
        raise HTTPException(status_code=404, detail="unknown MCP server")
    result = await _mcp_probe(target["mcp"]["host"], _mcp_credential(name, targets))
    bus.publish("mcp.server.test", {"server": name, "ok": result["ok"]})
    return result


# ---------------------------------------------------------------------- chats


@app.get("/api/chats", dependencies=[Depends(require_user)])
async def list_chats() -> dict[str, Any]:
    return {"chats": db.list_chats()}


@app.post("/api/chats", dependencies=[Depends(require_user)])
async def create_chat(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    chat = db.create_chat(
        title=(payload.get("title") or "New chat").strip()[:120],
        agent_set=payload.get("agent_set") or runtime("default_agent_set"),
        model=payload.get("model") or runtime("default_model"),
        tools=payload.get("tools"),
    )
    bus.publish("chat.created", {"chat_id": chat["id"], "title": chat["title"]})
    return chat


@app.patch("/api/chats/{chat_id}", dependencies=[Depends(require_user)])
async def update_chat(chat_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    if not db.get_chat(chat_id):
        raise HTTPException(status_code=404, detail="chat not found")
    fields: dict[str, Any] = {}
    if "title" in payload:
        fields["title"] = (payload.get("title") or "Untitled").strip()[:120]
    if "agent_set" in payload:
        fields["agent_set"] = payload.get("agent_set") or runtime("default_agent_set")
    if "model" in payload:
        fields["model"] = payload.get("model") or None
    if "tools" in payload:
        selected = payload.get("tools")
        fields["tools"] = [str(name) for name in selected] if isinstance(selected, list) else None
    return db.update_chat(chat_id, fields)  # type: ignore[return-value]


@app.get("/api/chats/{chat_id}", dependencies=[Depends(require_user)])
async def get_chat(chat_id: str) -> dict[str, Any]:
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")
    return {"chat": chat, "messages": db.list_messages(chat_id)}


@app.delete("/api/chats/{chat_id}", dependencies=[Depends(require_user)])
async def delete_chat(chat_id: str) -> dict[str, Any]:
    db.delete_chat(chat_id)
    bus.publish("chat.deleted", {"chat_id": chat_id})
    return {"ok": True}


@app.post("/api/chats/{chat_id}/messages", dependencies=[Depends(require_user)])
async def send_message(chat_id: str, payload: dict[str, Any] = Body(...)) -> StreamingResponse:
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    history = build_history(db.list_messages(chat_id), max_chars=history_budget(chat.get("model")))
    user_message = db.add_message(chat_id, "user", text)
    if chat["title"] in {"New chat", ""} and not history:
        db.execute("UPDATE chats SET title = ? WHERE id = ?", (summarise_for_title(text), chat_id))

    job = agent_job(
        prompt=text,
        mode="interactive",
        history=history,
        agent_set=chat["agent_set"],
        model=chat.get("model"),
        tools=chat.get("tools"),
        run_id=f"chat-{chat_id}",
    )

    async def generator():
        yield sse({"type": "user_message", "message": user_message})
        collected: list[str] = []
        failure: str | None = None
        tools: list[str] = []
        try:
            async for event in stream_agent(job):
                kind = event.get("type")
                if kind == "delta":
                    collected.append(event.get("text", ""))
                elif kind == "tool":
                    tools.append(event.get("name", "?"))
                elif kind == "error":
                    failure = event.get("message")
                elif kind == "result":
                    _remember_agent_result(bool(event.get("ok")))
                    if not collected and event.get("text"):
                        collected.append(event["text"])
                yield sse(event)
        except Exception as exc:  # noqa: BLE001 - always close the stream cleanly
            failure = str(exc)
            yield sse({"type": "error", "message": failure})

        content = "".join(collected).strip()
        if not content and failure:
            content = f"The agent could not answer: {failure}"
        assistant = db.add_message(
            chat_id, "assistant", content or "(no answer)", {"tools": tools, "error": failure}
        )
        bus.publish("chat.answered", {"chat_id": chat_id, "ok": failure is None})
        yield sse({"type": "done", "message": assistant})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chats/{chat_id}/promote", dependencies=[Depends(require_user)])
async def promote(chat_id: str) -> dict[str, Any]:
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="chat not found")
    messages = db.list_messages(chat_id)
    if not messages:
        raise HTTPException(status_code=400, detail="nothing to promote yet")
    bus.publish("promote.start", {"chat_id": chat_id})
    draft = await promote_chat(chat, messages)
    db.execute("UPDATE chats SET promoted_to = ? WHERE id = ?", (draft["name"], chat_id))
    bus.publish("promote.draft", {"chat_id": chat_id, "workflow": draft["name"]})
    return draft


# ------------------------------------------------------------------ workflows


@app.get("/api/workflows", dependencies=[Depends(require_user)])
async def list_workflows() -> dict[str, Any]:
    workflows = await authoring.list_workflows()
    schedules: list[dict[str, Any]] = []
    try:
        schedules = await temporal.schedules()
    except Exception:  # noqa: BLE001 - schedules are extra, not essential
        schedules = []
    by_workflow = {item["workflow"]: item for item in schedules}
    for workflow in workflows:
        workflow["schedule"] = by_workflow.get(workflow["name"])
        workflow["settings"] = db.workflow_settings(workflow["name"])
    return {"workflows": workflows}


@app.get("/api/workflows/{name}", dependencies=[Depends(require_user)])
async def get_workflow(name: str) -> dict[str, Any]:
    workflow = await authoring.get_workflow(name)
    workflow["runs"] = db.list_runs(name, limit=25)
    workflow["settings"] = db.workflow_settings(name)
    try:
        workflow["schedule"] = next(
            (item for item in await temporal.schedules() if item["workflow"] == name), None
        )
    except Exception:  # noqa: BLE001 - schedules are extra, not essential
        workflow["schedule"] = None
    return workflow


@app.patch("/api/workflows/{name}/settings", dependencies=[Depends(require_user)])
async def patch_workflow_settings(name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if "disabled" in payload:
        fields["disabled"] = bool(payload["disabled"])
    if "chat_mode" in payload:
        mode = payload["chat_mode"]
        if mode not in {"same", "new"}:
            raise HTTPException(status_code=400, detail="chat_mode must be 'same' or 'new'")
        fields["chat_mode"] = mode
    updated = db.set_workflow_settings(name, fields)
    bus.publish("workflow.settings", {"workflow": name, **fields})
    return updated


@app.delete("/api/workflows/{name}", dependencies=[Depends(require_user)])
async def delete_workflow(name: str) -> dict[str, Any]:
    result = await authoring.delete_workflow(name)
    db.forget_workflow(name)
    bus.publish("workflow.deleted", {"workflow": name})
    asyncio.create_task(_restart_worker())
    return result


@app.post("/api/workflows/validate", dependencies=[Depends(require_user)])
async def validate_workflow(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return await authoring.validate(payload.get("name", ""), payload.get("code", ""))


@app.post("/api/workflows/{name}/run", dependencies=[Depends(require_user)])
async def run_workflow(name: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    return await _start_run(name, payload.get("input") or {}, trigger="manual")


@app.post("/api/workflows/{name}/schedule", dependencies=[Depends(require_user)])
async def schedule_workflow(name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    cron = (payload.get("cron") or "").strip()
    if not cron:
        raise HTTPException(status_code=400, detail="cron is required, e.g. '0 8 * * *'")
    result = await temporal.set_schedule(name, cron, payload.get("input") or {})
    bus.publish("workflow.scheduled", {"workflow": name, "cron": cron})
    return result


@app.delete("/api/workflows/{name}/schedule", dependencies=[Depends(require_user)])
async def unschedule_workflow(name: str) -> dict[str, Any]:
    await temporal.delete_schedule(name)
    bus.publish("workflow.unscheduled", {"workflow": name})
    return {"ok": True}


# --------------------------------------------------------------------- drafts


@app.get("/api/drafts", dependencies=[Depends(require_user)])
async def list_drafts() -> dict[str, Any]:
    return {"drafts": await authoring.list_drafts()}


@app.get("/api/drafts/{name}", dependencies=[Depends(require_user)])
async def get_draft(name: str) -> dict[str, Any]:
    return await authoring.get_draft(name)


@app.post("/api/drafts/{name}/approve", dependencies=[Depends(require_user)])
async def approve_draft(name: str) -> dict[str, Any]:
    published = await authoring.publish(name)
    bus.publish("workflow.published", {"workflow": name})
    restart = await _restart_worker()
    published["worker_restart"] = restart
    return published


@app.delete("/api/drafts/{name}", dependencies=[Depends(require_user)])
async def discard_draft(name: str) -> dict[str, Any]:
    result = await authoring.discard(name)
    bus.publish("workflow.draft_discarded", {"workflow": name})
    return result


# ----------------------------------------------------------------------- runs


async def _start_run(name: str, payload: dict[str, Any], trigger: str) -> dict[str, Any]:
    if db.workflow_settings(name)["disabled"]:
        raise HTTPException(status_code=409, detail=f"{name} is disabled")
    workflow = await authoring.get_workflow(name)
    manifest = workflow.get("manifest") or {}
    problems = input_problems(manifest.get("inputs"), payload)
    if problems:
        raise HTTPException(status_code=400, detail={"workflow": name, "input": problems})
    workflow_id = f"{name}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    started = await temporal.start(
        name, workflow_id, payload, timeout_minutes=manifest.get("timeout_minutes", 30)
    )
    db.record_run(name, started["workflow_id"], started.get("run_id"), trigger, payload)
    bus.publish("run.started", {"workflow": name, "workflow_id": workflow_id, "trigger": trigger})
    asyncio.create_task(_watch_run(name, workflow_id))
    return {"workflow": name, **started, "trigger": trigger}


def _render_result(result: Any) -> str:
    """Prose stays prose; anything shaped arrives as JSON you can read and quote."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and len(result) == 1:
        only = next(iter(result.values()))
        if isinstance(only, str):
            return only
    return f"```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```"


async def _deliver_to_chat(name: str, workflow_id: str, status: str, result: Any) -> None:
    """A finished run lands in a chat, so its output can be talked to."""
    config = db.workflow_settings(name)
    workflow_title = name
    # A deleted workflow still deserves to deliver its last result.
    with contextlib.suppress(Exception):
        workflow_title = (await authoring.get_workflow(name)).get("title") or name

    chat_id = config["chat_id"] if config["chat_mode"] == "same" else None
    if chat_id and not db.get_chat(chat_id):
        chat_id = None
    if not chat_id:
        suffix = time.strftime("%d %b %H:%M") if config["chat_mode"] == "new" else ""
        chat = db.create_chat(
            title=f"{workflow_title} {suffix}".strip()[:120],
            agent_set=runtime("default_agent_set"),
            model=runtime("default_model"),
        )
        chat_id = chat["id"]
        if config["chat_mode"] == "same":
            db.set_workflow_settings(name, {"chat_id": chat_id})

    body = _render_result(result)
    if not body:
        body = f"`{status}`"
    db.add_message(
        chat_id,
        "assistant",
        body,
        {"run": {"workflow": name, "workflow_id": workflow_id, "status": status}},
    )
    bus.publish("chat.answered", {"chat_id": chat_id, "ok": status == "completed"})


async def _watch_run(name: str, workflow_id: str) -> None:
    """Follow a run so the UI sees an end state without polling Temporal."""
    for _ in range(240):  # up to ~20 minutes at 5s
        await asyncio.sleep(5)
        try:
            info = await temporal.describe(workflow_id)
        except Exception:  # noqa: BLE001, S112 - a describe that fails is just a slow answer
            continue
        if info["status"] in {"RUNNING", "UNKNOWN"}:
            continue
        result: Any = None
        if info["status"] == "COMPLETED":
            try:
                result = await temporal.result(workflow_id, timeout=10)
            except Exception:  # noqa: BLE001
                result = None
        status = info["status"].lower()
        db.update_run(workflow_id, status, result)
        with contextlib.suppress(Exception):
            await _deliver_to_chat(name, workflow_id, status, result)
        bus.publish(
            "run.finished",
            {"workflow": name, "workflow_id": workflow_id, "status": info["status"]},
        )
        return


@app.get("/api/runs", dependencies=[Depends(require_user)])
async def list_runs(workflow: str | None = None) -> dict[str, Any]:
    local = db.list_runs(workflow)
    try:
        remote = await temporal.recent(50)
    except Exception:  # noqa: BLE001
        remote = []
    by_id = {item["workflow_id"]: item for item in remote}
    for run in local:
        live = by_id.get(run["workflow_id"])
        if live:
            run["status"] = live["status"].lower()
            run["start_time"] = live["start_time"]
            run["close_time"] = live["close_time"]
    return {"runs": local, "temporal": remote}


@app.get("/api/runs/{workflow_id}", dependencies=[Depends(require_user)])
async def get_run(workflow_id: str) -> dict[str, Any]:
    info = await temporal.describe(workflow_id)
    row = db.one("SELECT * FROM runs WHERE workflow_id = ?", (workflow_id,))
    if row:
        row["input"] = json.loads(row["input"] or "{}")
        row["result"] = json.loads(row["result"]) if row["result"] else None
    if info["status"] == "COMPLETED" and (not row or row.get("result") is None):
        try:
            info["result"] = await temporal.result(workflow_id, timeout=10)
        except Exception:  # noqa: BLE001
            info["result"] = None
    return {"run": row, "temporal": info}


@app.post("/api/runs/{workflow_id}/cancel", dependencies=[Depends(require_user)])
async def cancel_run(workflow_id: str) -> dict[str, Any]:
    await temporal.cancel(workflow_id)
    return {"ok": True}


@app.post("/api/runs/{workflow_id}/terminate", dependencies=[Depends(require_user)])
async def terminate_run(workflow_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """For a run that cannot be asked nicely, because its worker cannot load it."""
    await temporal.terminate(workflow_id, payload.get("reason") or "terminated from the app")
    db.update_run(workflow_id, "terminated")
    bus.publish("run.terminated", {"workflow_id": workflow_id})
    return {"ok": True}


# -------------------------------------------------------------------- trigger


@app.post("/api/triggers/{name}")
async def trigger(name: str, request: Request, token: str | None = Query(default=None)) -> dict[str, Any]:
    """Triggers come in here and nowhere else."""
    if settings.auth_enabled:
        header = request.headers.get("authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else token
        if not _token_matches(supplied, settings.app_token):
            raise HTTPException(status_code=401, detail="unauthorized")
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a webhook may send nothing at all
        payload = {}
    if not isinstance(payload, dict):
        payload = {"payload": payload}
    return await _start_run(name, payload, trigger="webhook")


# ------------------------------------------------------------------- internal


@app.post("/internal/agent/call", dependencies=[Depends(require_internal)])
async def internal_agent_call(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Activity mode. The worker asks, the backend runs one container, one object comes back."""
    job = agent_job(
        prompt=payload.get("prompt", ""),
        mode="activity",
        system_prompt=payload.get("system_prompt"),
        history=payload.get("history") or [],
        output_schema=payload.get("output_schema"),
        agent_set=payload.get("agent_set"),
        run_id=payload.get("run_id", ""),
        timeout_seconds=int(payload.get("timeout_seconds") or 900),
    )
    bus.publish("agent.activity", {"run_id": job["run_id"], "agent_set": job["agent_set"]})
    result = await call_agent(job)
    _remember_agent_result(bool(result.get("ok")))
    return result


@app.post("/internal/events", dependencies=[Depends(require_internal)])
async def internal_event(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    kind = payload.get("kind", "worker.event")
    body = payload.get("payload") or {}
    db.add_event(payload.get("scope", "worker"), kind, body)
    bus.publish(kind, body)
    return {"ok": True}


@app.post("/internal/worker/restart", dependencies=[Depends(require_internal)])
async def internal_worker_restart() -> dict[str, Any]:
    return await _restart_worker()


async def _restart_worker() -> dict[str, Any]:
    try:
        result = await broker.restart_worker()
        bus.publish("worker.restarted", result)
        return {"ok": True, **result}
    except Exception as exc:  # noqa: BLE001 - a failed restart must be visible, not fatal
        bus.publish("worker.restart_failed", {"error": str(exc)[:200]})
        return {"ok": False, "error": str(exc)[:200]}


# ------------------------------------------------------------------ artifacts


@app.get("/api/artifacts/{name}", dependencies=[Depends(require_user)])
async def get_artifact(name: str) -> Response:
    safe = os.path.basename(name)
    path = Path(settings.artifacts_dir) / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return PlainTextResponse(path.read_text(encoding="utf-8", errors="replace"))


# --------------------------------------------------------------- frontend pass


_EXCLUDED_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding"}


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def frontend(path: str, request: Request) -> Response:
    """One door: the SPA is served through the backend, not published itself."""
    if path.startswith(("api/", "internal/")):
        return JSONResponse({"detail": "not found"}, status_code=404)
    target = f"{settings.frontend_web_url.rstrip('/')}/{path}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            upstream = await client.request(request.method, target, params=dict(request.query_params))
    except httpx.HTTPError as exc:
        return PlainTextResponse(f"frontend unavailable: {exc}", status_code=502)
    headers = {key: value for key, value in upstream.headers.items() if key.lower() not in _EXCLUDED_HEADERS}
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )
