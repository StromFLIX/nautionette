"""Validated, instance-wide defaults for future agent-created Git commits."""

from __future__ import annotations

import re
from typing import Any

DEFAULTS = {
    "git_authorship_mode": "automation",
    "git_human_name": "",
    "git_human_email": "",
    "git_automation_name": "Nautionette",
    "git_automation_email": "nautionette@users.noreply.github.com",
}
MODES = {"automation", "human_author", "human_author_bot_coauthor", "bot_author_human_coauthor"}


def validate(values: dict[str, Any]) -> dict[str, str]:
    """Validate the merged settings before saving anything (including partial updates)."""
    result = {}
    for key, default in DEFAULTS.items():
        value = values.get(key, default)
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError(f"{key} must be a single line without control characters")
        result[key] = value.strip()
    if result["git_authorship_mode"] not in MODES:
        raise ValueError("Unknown Git authorship mode")
    for identity in ("human", "automation"):
        name = result[f"git_{identity}_name"]
        email = result[f"git_{identity}_email"]
        required = identity == "automation" or result["git_authorship_mode"] != "automation"
        if required and (not name or not email):
            raise ValueError(f"Git {identity} name and email are required for this authorship mode")
        if len(name) > 200 or any(char in name for char in "<>"):
            raise ValueError(f"Git {identity} name must be at most 200 characters and cannot contain < or >")
        if email and (len(email) > 254 or not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", email)):
            raise ValueError(f"Git {identity} email must be a valid email address")
    return result


def for_job() -> dict[str, str]:
    # Import lazily: runtime uses our defaults.
    from .runtime import runtime

    return {
        key.removeprefix("git_"): value
        for key, value in validate({key: runtime(key) for key in DEFAULTS}).items()
    }
