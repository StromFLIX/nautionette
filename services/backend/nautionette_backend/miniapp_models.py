"""Infrastructure contracts only. Frontend HTML/CSS/JavaScript is not a widget schema."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

AppID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")]

# Explicit orchestration capabilities, not a general host-administration grant.
# Canonical tool names only; aliases and arbitrary run inspection remain denied.
WORKFLOW_TOOLS = {"workflows_run_workflow": "name", "workflows_list_runs": "workflow"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Frontend(StrictModel):
    html: str = Field(max_length=131072)
    css: str = Field(default="", max_length=65536)
    js: str = Field(default="", max_length=131072)


class MCPGrant(StrictModel):
    tool: str = Field(pattern=r"^[a-zA-Z0-9_:.\-/]{1,160}$")
    arguments: dict = Field(
        default_factory=dict,
        description=(
            "Required fixed argument subset, recursively matched; e.g. owner/repo or a resource UUID. "
            "Missing constraints do NOT imply resource scoping."
        ),
    )

    @model_validator(mode="after")
    def no_host_administration(self):
        name = self.tool.lower().replace(":", "_").replace("/", "_").replace(".", "_").replace("-", "_")
        if workflow_key := WORKFLOW_TOOLS.get(self.tool):
            workflow = self.arguments.get(workflow_key)
            if not isinstance(workflow, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", workflow):
                raise ValueError("Workflow tools require an exact fixed workflow name")
        elif name.startswith(("backend_", "workflows_", "workflow_")) or any(
            word in name for word in ("env_values", "private_key", "get_token", "list_tokens")
        ):
            raise ValueError("Host administration and credential-returning tools cannot be granted")
        if len(json.dumps(self.arguments, allow_nan=False).encode()) > 8192:
            raise ValueError("Argument constraints exceed 8 KiB")
        return self


class HTTPGrant(StrictModel):
    origin: str = Field(max_length=250)
    path_prefix: str = Field(default="/", pattern=r"^/[a-zA-Z0-9/_.-]*$", max_length=250)
    methods: list[Literal["GET", "POST", "PUT", "PATCH", "DELETE"]] = Field(
        default_factory=lambda: ["GET"], min_length=1, max_length=5
    )

    @model_validator(mode="after")
    def https_origin(self):
        parsed = urlsplit(self.origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.port not in (443, None)
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or self.origin != f"https://{parsed.hostname}"
        ):
            raise ValueError("Use a lowercase HTTPS origin on port 443, with no path or credentials")
        if ".." in self.path_prefix.split("/"):
            raise ValueError("Path traversal is not a permission")
        return self


class Permissions(StrictModel):
    mcp: list[MCPGrant] = Field(default_factory=list, max_length=16)
    http: list[HTTPGrant] = Field(default_factory=list, max_length=8)


class Backend(StrictModel):
    source: str = Field(
        min_length=1,
        max_length=65536,
        description=(
            "Rust app.rs module using the fixed, locked platform SDK. "
            "No Dockerfile, Cargo manifest, arbitrary image or build command."
        ),
    )
    mode: Literal["on-demand", "background"] = "on-demand"
    memory_mb: Literal[64, 128, 256] = 64
    permissions: Permissions = Field(default_factory=Permissions)


class Manifest(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    icon: str = Field(default="apps", pattern=r"^[a-z][a-z0-9_]{0,47}$", description="Material icon name")
    image: str = Field(
        default="",
        max_length=32768,
        pattern=r"^(?:|data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2})$",
        description="Optional small inline PNG/JPEG/WebP avatar. No SVG or remote URLs.",
    )
    agent_id: str | None = Field(default=None, max_length=64)
    pinned: bool = False
    frontend: Frontend
    backend: Backend | None = None

    @model_validator(mode="after")
    def byte_limits(self):
        if len(json.dumps(self.model_dump(), ensure_ascii=False, allow_nan=False).encode()) > 320 * 1024:
            raise ValueError("A mini-app release may not exceed 320 KiB")
        if self.backend and len(self.backend.source.encode()) > 65536:
            raise ValueError("Rust source may not exceed 64 KiB")
        return self


class Publication(StrictModel):
    expected_revision: int = Field(ge=0, le=999999)
    manifest: Manifest


class AppRequest(StrictModel):
    revision: int = Field(ge=1)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str = Field(pattern=r"^/[a-zA-Z0-9/_.-]*$", max_length=250)
    body: dict | list | str | int | float | bool | None = None
    request_id: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def bounded(self):
        if (
            ".." in self.path.split("/")
            or len(json.dumps(self.model_dump(), allow_nan=False).encode()) > 48 * 1024
        ):
            raise ValueError("Invalid or oversized app request")
        return self


class Restore(StrictModel):
    expected_revision: int = Field(ge=1)
    revision: int = Field(ge=1)
    pinned: bool = False


class EditApp(StrictModel):
    expected_revision: int = Field(ge=1)
    pinned: bool | None = None
    pin_revision: int | None = Field(default=None, ge=1)
    archived: bool | None = None


class ValidateApp(StrictModel):
    manifest: Manifest
    compile: bool = False
