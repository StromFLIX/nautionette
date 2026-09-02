"""Bits every outbound client shares."""

from __future__ import annotations

from ..config import settings


def internal_headers() -> dict[str, str]:
    if settings.internal_token:
        return {"X-Internal-Token": settings.internal_token}
    return {}


def upstream_problem(name: str, credential: str, status: int | None, body: str) -> str:
    """One actionable sentence, whichever provider refused the call."""
    text = body.lower()
    if "token not found" in text:
        return f"agentgateway found no {name} credential. Add {credential} and try again."
    if "copilot-integration-id" in text:
        return f"{name} rejected the configured integration ID."
    # xAI answers a rejected key with 400, so the body decides alongside the status.
    if status in {401, 403} or "api key" in text or "unauthorized" in text:
        return f"{name} rejected {credential}."
    suffix = f" (HTTP {status})" if status else ""
    return f"agentgateway could not reach {name}{suffix}."
