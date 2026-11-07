"""Read-only staging check: matching backend, authenticated helper and desktop API.

No CLI is launched, license accepted or OAuth/account data accessed. Credentials
come only from CI environment and neither responses nor exception text are logged.
"""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def check(base: str, token: str) -> dict:
    try:
        parsed = urlsplit(base)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not token
            or not token.isascii()
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "Configure a valid HTTPS APP_URL and APP_TOKEN for the CLI OAuth staging check"
        ) from exc
    request = Request(  # noqa: S310 - validated configured CI application origin
        base.rstrip("/") + "/api/mcp-oauth/capabilities", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:  # noqa: S310
            body = response.read(65537)
        if len(body) > 65536:
            raise ValueError
        value = json.loads(body)
        if value.get("available") is not True or value.get("desktop_bridge_version") != 1:
            raise ValueError
    except (HTTPError, URLError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError(
            "CLI OAuth staging check failed: deploy the matching authenticated gateway/backend images"
        ) from exc
    return {"available": True, "desktop_bridge_version": 1}


if __name__ == "__main__":
    try:
        result = check(os.environ.get("APP_URL", ""), os.environ.get("APP_TOKEN", ""))
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(1) from None
    print("CLI OAuth staging compatibility: " + json.dumps(result))
