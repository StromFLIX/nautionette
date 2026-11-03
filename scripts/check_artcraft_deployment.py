"""Read-only deployment diagnostics. Never print tokens or arbitrary service errors."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def diagnostic(state: dict) -> dict:
    broker = next((item for item in state.get("components", []) if item.get("name") == "broker"), {})
    detail = broker.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    error = str(detail.get("error") or "").lower()
    known = {
        "network_address_pool": ("pool", "subnet"),
        "storage": ("space", "storage limit"),
        "timeout": ("timeout", "timed out"),
        "native_verification": ("verification", "checksum", "compatibility"),
        "permission": ("permission", "denied"),
        "image": ("image", "not found"),
    }
    allowed = {"ok", "ready", "building", "pending", "failed", "missing", "degraded", "down"}
    return {
        "broker_status": broker.get("status") if broker.get("status") in allowed else "unknown",
        "image_status": detail.get("image_status") if detail.get("image_status") in allowed else "unknown",
        "error_categories": [
            category for category, terms in known.items() if any(term in error for term in terms)
        ],
        "has_error": bool(error),
        "workers_ready": detail.get("workers", {}).get("status") == "ready",
        "agent_sets_ready": bool(state.get("agent_sets"))
        and all(item.get("ready") is True for item in state["agent_sets"]),
    }


def wait_for_ready(fetch, *, timeout=180, interval=5):
    """Workflow cleanup restarts workers asynchronously; retry GETs, not mutations."""
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Staging readiness timed out; artifacts and workers must both be ready")
        try:
            result = diagnostic(fetch(min(30, remaining)))
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise RuntimeError("Staging readiness authentication failed") from None
            result = {"system_status": "unavailable"}
        except Exception:
            result = {"system_status": "unavailable"}
        if result != previous:
            print(json.dumps(result), flush=True)
            previous = result
        if (
            result.get("broker_status") == "ok"
            and result.get("image_status") == "ready"
            and result.get("workers_ready") is True
            and result.get("agent_sets_ready") is True
        ):
            return result
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def main():
    url, token = os.environ["APP_URL"].rstrip("/"), os.environ["APP_TOKEN"]
    if not url.startswith("https://"):
        raise ValueError("APP_URL must use HTTPS")
    request = urllib.request.Request(  # noqa: S310 - HTTPS is checked above; deployment-owned URL
        url + "/api/system", headers={"Authorization": "Bearer " + token}
    )
    opener = urllib.request.build_opener(SafeRedirect())

    def fetch(timeout):
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)

    try:
        wait_for_ready(fetch)
    except RuntimeError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
