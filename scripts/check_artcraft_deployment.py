"""Read-only deployment diagnostics. Never print tokens or arbitrary service errors."""

from __future__ import annotations

import json
import os
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


def main():
    url, token = os.environ["APP_URL"].rstrip("/"), os.environ["APP_TOKEN"]
    if not url.startswith("https://"):
        raise ValueError("APP_URL must use HTTPS")
    request = urllib.request.Request(  # noqa: S310 - HTTPS is checked above; deployment-owned URL
        url + "/api/system", headers={"Authorization": "Bearer " + token}
    )
    try:
        with urllib.request.build_opener(SafeRedirect()).open(request, timeout=30) as response:
            result = diagnostic(json.load(response))
    except Exception:
        raise SystemExit("Creative-app diagnostics: system status unavailable") from None
    print(json.dumps(result), flush=True)
    if result["broker_status"] != "ok" or not result["agent_sets_ready"]:
        raise SystemExit("Creative-app artifacts are not ready; see the safe diagnostic categories above")


if __name__ == "__main__":
    main()
