"""Versioned declarative dashboard contracts. No executable expressions or CSS."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, model_validator

Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Pointer = Annotated[str, Field(max_length=512, pattern=r"^(|/.*)$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Binding(Contract):
    source: Key
    path: Pointer = ""


class Condition(Binding):
    equals: Any


class Source(Contract):
    kind: Literal["literal", "snapshot", "tool", "workflow-result"]
    data: Any = None
    tool: str = Field(default="", max_length=256)
    arguments: dict[str, Any] = Field(default_factory=dict)
    workflow: str = Field(default="", pattern=r"^([a-z][a-z0-9_]{0,63})?$")
    refresh_seconds: int = Field(default=60, ge=15, le=3600)
    stale_seconds: int = Field(default=180, ge=15, le=86400)
    select: Pointer = ""
    fields: dict[Key, Pointer] = Field(default_factory=dict, max_length=24)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def source_contract(self):
        if self.stale_seconds < self.refresh_seconds:
            raise ValueError("stale_seconds must be at least refresh_seconds")
        if self.kind == "tool" and not self.tool:
            raise ValueError("tool sources need a discovered tool name")
        if self.kind == "workflow-result" and not self.workflow:
            raise ValueError("workflow-result sources need a workflow name")
        check_schema(self.output_schema)
        return self


class Action(Contract):
    label: str = Field(min_length=1, max_length=80)
    workflow: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    inputs: dict[str, Any] = Field(default_factory=dict)
    bindings: dict[Key, Binding] = Field(default_factory=dict, max_length=24)
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False}
    )
    conditions: list[Condition] = Field(default_factory=list, max_length=16)
    confirm: bool = True
    confirmation: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def input_contract(self):
        check_schema(self.input_schema)
        if (
            self.input_schema.get("type") != "object"
            or self.input_schema.get("additionalProperties") is not False
        ):
            raise ValueError("action input_schema must be an object with additionalProperties: false")
        if set(self.input_schema.get("properties", {})) & (self.inputs.keys() | self.bindings.keys()):
            raise ValueError("form inputs cannot override fixed inputs or source bindings")
        if self.inputs.keys() & self.bindings.keys():
            raise ValueError("an input cannot be both fixed and source-bound")
        return self


class Part(Contract):
    component: Literal["metric", "status", "text", "list", "table", "key-value", "action", "workflow"]
    props: dict[str, Any] = Field(default_factory=dict)
    bindings: dict[Key, Pointer] = Field(default_factory=dict, max_length=24)


class Recipe(Contract):
    agent_id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    parts: list[Part] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def recipe_contract(self):
        bounded(self.model_dump())
        no_secrets(self.model_dump())
        return self


class Widget(Contract):
    id: Key
    component: str = Field(min_length=1, max_length=128)
    title: str = Field(default="", max_length=120)
    source: Key | None = None
    props: dict[str, Any] = Field(default_factory=dict)
    bindings: dict[Key, Pointer] = Field(default_factory=dict, max_length=24)
    span: int = Field(default=4, ge=1, le=12)


class Manifest(Contract):
    schema_version: Literal[1] = 1
    agent_id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    pinned: bool = True
    sources: dict[Key, Source] = Field(default_factory=dict, max_length=24)
    actions: dict[Key, Action] = Field(default_factory=dict, max_length=16)
    widgets: list[Widget] = Field(default_factory=list, max_length=48)

    @model_validator(mode="after")
    def references(self):
        if len({item.id for item in self.widgets}) != len(self.widgets):
            raise ValueError("widget IDs must be unique")
        for widget in self.widgets:
            if widget.source and widget.source not in self.sources:
                raise ValueError(f"unknown source: {widget.source}")
            if widget.bindings and not widget.source:
                raise ValueError("widget bindings need a source")
        for action in self.actions.values():
            for binding in [*action.bindings.values(), *action.conditions]:
                if binding.source not in self.sources:
                    raise ValueError(f"unknown action source: {binding.source}")
        bounded(self.model_dump())
        no_secrets(self.model_dump())
        return self


class DashboardWrite(Contract):
    expected_revision: int = Field(ge=0)
    manifest: Manifest


class RecipeWrite(Contract):
    expected_revision: int = Field(ge=0)
    recipe: Recipe


class Restore(Contract):
    expected_revision: int = Field(ge=1)
    revision: int = Field(ge=1)


class Pin(Contract):
    expected_revision: int = Field(ge=1)
    pinned: bool


class PublishData(Contract):
    expected_revision: int = Field(ge=1)
    data: Any


class Invoke(Contract):
    expected_revision: int = Field(ge=1)
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,80}$")
    inputs: dict[str, Any] = Field(default_factory=dict)
    context_hash: str = Field(default="", max_length=64)
    confirmed: bool = False

    @model_validator(mode="after")
    def public_inputs(self):
        bounded(self.inputs, 32768)
        no_secrets(self.inputs)
        return self


def no_secrets(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(
                r"password|secret|credential|authorization|access.?token|api.?key|private.?key", key, re.I
            ):
                raise ValueError(
                    "Credentials belong in configured integrations, not dashboard manifests or inputs"
                )
            no_secrets(child)
    elif isinstance(value, list):
        for child in value:
            no_secrets(child)


def bounded(value: Any, limit: int = 131072) -> None:
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 16:
            raise ValueError("Dashboard data nesting exceeds 16 levels")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    if len(json.dumps(value, allow_nan=False).encode()) > limit:
        raise ValueError(f"dashboard data exceeds {limit} bytes")


def check_schema(schema: dict[str, Any], *, local_refs: bool = False) -> None:
    bounded(schema, 16384)

    def visit(value, depth=0):
        if depth > 16:
            raise ValueError("schema nesting exceeds 16 levels")
        if isinstance(value, dict):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if key in value and (not local_refs or not str(value[key]).startswith("#/")):
                    raise ValueError("dashboard schemas must be self-contained (no remote references)")
            for child in value.values():
                visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth + 1)

    visit(schema)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError:
        raise ValueError("Invalid dashboard JSON schema") from None


def pointer(data: Any, path: str) -> Any:
    if path == "":
        return data
    for token in path[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if key in {"__proto__", "constructor", "prototype"}:
            raise ValueError("unsafe pointer")
        if isinstance(data, list):
            if not token.isdecimal() or int(token) >= len(data):
                raise ValueError("pointer index is unavailable")
            data = data[int(token)]
        elif isinstance(data, dict) and key in data:
            data = data[key]
        else:
            raise ValueError("pointer field is unavailable")
    return data


# Shared semantic props; rendering never accepts HTML, JS, CSS or template strings.
STRING = {"type": "string", "maxLength": 4000}
SCALAR = {"type": ["string", "number", "boolean", "null"]}
TONE = {"enum": ["neutral", "accent", "success", "warning", "danger"]}
COMMON = {"description": STRING, "href": STRING, "tone": TONE}
COMPONENTS = {
    "metric": {"value": SCALAR, "label": STRING, **COMMON},
    "status": {"value": SCALAR, "label": STRING, **COMMON},
    "text": {"text": STRING, **COMMON},
    "list": {"items": {"type": "array", "maxItems": 100}, **COMMON},
    "table": {
        "items": {"type": "array", "maxItems": 100},
        "columns": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {"key": STRING, "label": STRING},
                "required": ["key", "label"],
                "additionalProperties": False,
            },
        },
        **COMMON,
    },
    "key-value": {"items": {"type": "object", "maxProperties": 32}, **COMMON},
    "action": {"action": {"type": "string", "pattern": r"^[a-z][a-z0-9_-]{0,63}$"}, **COMMON},
    "workflow": {"action": STRING, "workflow": STRING, **COMMON},
}


def props_schema(component: str, partial: bool = False) -> dict[str, Any]:
    result = {"type": "object", "properties": COMPONENTS[component], "additionalProperties": False}
    if not partial:
        required = {
            "metric": "value",
            "status": "value",
            "text": "text",
            "list": "items",
            "table": "items",
            "key-value": "items",
            "action": "action",
        }.get(component)
        if required:
            result["required"] = [required]
    return result
