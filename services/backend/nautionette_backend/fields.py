"""Declared form fields, and checking a payload against them.

Model integrations and MCP servers are both configured by filling in a list of
declared fields, so both the schema the clients render and the validation the
app performs come from the same place.
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

SLUG = r"[a-z0-9][a-z0-9-]{0,23}"
ENV_NAME = r"[A-Z][A-Z0-9_]{0,63}"
HTTPS_URL = r"https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/-]*)?"
# Either the secret itself, or $MY_VARIABLE naming one set on agentgateway.
SECRET = rf"(?:\${ENV_NAME}|\S(?:.*\S)?)"


def public_host(url: str) -> None:
    """A user-supplied endpoint must be public: the gateway can also see this network."""
    host = urlsplit(url).hostname or ""
    with contextlib.suppress(ValueError):
        address = ipaddress.ip_address(host)
        if not address.is_global:
            raise HTTPException(status_code=400, detail="the base URL must be a public address")
    if "." not in host or host.endswith((".local", ".internal")):
        raise HTTPException(status_code=400, detail="the base URL must be a public hostname")


def normalise(fields: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, str]:
    """Trim, default and check every declared field. Anything undeclared is dropped."""
    config: dict[str, str] = {}
    for field in fields:
        value = str(payload.get(field["key"]) or field.get("default", "")).strip()
        if not value and field.get("optional"):
            config[field["key"]] = ""
            continue
        if not re.fullmatch(field["pattern"], value):
            raise HTTPException(status_code=400, detail=f"invalid value for {field['label']}")
        if field.get("public"):
            public_host(value)
        config[field["key"]] = value
    return config
