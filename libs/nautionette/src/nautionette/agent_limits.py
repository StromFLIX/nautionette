"""Shared interpretation of per-chat runtime exemptions."""

from __future__ import annotations

from typing import Any


def chat_timeout_exempt(job: dict[str, Any]) -> bool:
    """Only an explicit exemption on an interactive chat turn bypasses the limit.

    Missing flags, truthy strings and workflow calls retain their usual budgets.
    This is runtime policy, not authentication; jobs arrive over internal APIs.
    """
    return (
        job.get("timeout_exempt") is True
        and job.get("mode") == "interactive"
        and bool(job.get("chat_id"))
        and bool(job.get("turn_id"))
    )
