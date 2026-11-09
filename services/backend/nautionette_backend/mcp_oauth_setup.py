"""Remember validated OAuth setup independently of short-lived credentials.

Retry/relogin reuses the last successful issuer/client/scope/callback instead of
requiring discovery or provider preset buttons again. Only public setup fields
are stored here; authorization codes, verifiers and tokens never are.
"""

from __future__ import annotations

import json
import time

from fastapi import HTTPException
from nautionette.brand import NAME

from . import mcp_http_oauth as hosted
from .db import db
from .pi_package_secrets import unseal

MAX_AGE = 86400
FIELDS = {
    "url",
    "resource",
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "client_id",
    "scope",
    "redirect_uri",
    "response_mode",
    "use_resource",
    "discovery_fingerprint",
    "client_registration_verified",
    "registration_endpoint",
    "response_modes_supported",
    "registration_outcome",
}
OVERRIDES = {"client_id", "scope", "redirect_uri", "response_mode"}


def save(name: str, profile: dict, *, validated_at: float | None = None):
    value = {key: profile[key] for key in FIELDS if key in profile}
    db.execute(
        "INSERT INTO mcp_oauth_setups VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
        "url=excluded.url,profile=excluded.profile,updated_at=excluded.updated_at",
        (name, profile["url"], json.dumps(value), time.time() if validated_at is None else validated_at),
    )


def saved(name: str, url: str | None = None) -> dict | None:
    row = db.one("SELECT * FROM mcp_oauth_setups WHERE name=?", (name,))
    if row and (url is None or row["url"] == url):
        return {"profile": json.loads(row["profile"]), "updated_at": row["updated_at"]}
    # Upgrade existing connected accounts from the previous UI without asking
    # users to re-enter credentials or repeat setup. Expired accounts have only
    # their safe reconnect profile; discovery will refill it once automatically.
    row = db.one("SELECT * FROM mcp_http_oauth WHERE name=?", (name,))
    if row:
        public = json.loads(row["profile"])
        if url is not None and public["url"] != url:
            return None
        if row["secret"]:
            profile = unseal(row["secret"], namespace=hosted.NAMESPACE)["profile"]
            profile = {**profile, "redirect_uri": profile["redirect_uri_template"]}
            save(name, profile)
            return {"profile": profile, "updated_at": time.time()}
        return {"profile": public, "updated_at": 0}
    return None


def apply(profile: dict, payload: dict) -> dict:
    result = {**profile, **{key: payload[key] for key in OVERRIDES if payload.get(key) not in (None, "")}}
    client = result.get("client_id", "")
    scope = result.get("scope", "")
    if not isinstance(client, str) or len(client) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in client):
        raise HTTPException(400, "Invalid public OAuth client ID")
    if not isinstance(scope, str) or len(scope) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in scope):
        raise HTTPException(400, "Invalid OAuth scopes")
    hosted.redirect_uri(result["redirect_uri"], ephemeral=True)
    if result["response_mode"] not in result.get("response_modes_supported", [profile["response_mode"]]):
        raise HTTPException(400, "Provider does not support this callback mode")
    return result


def retain_registration_fence(profile: dict, previous: dict | None):
    prior = previous["profile"] if previous else {}
    if prior.get("registration_outcome") in {"pending", "uncertain", "rejected"} and all(
        prior.get(key) == profile.get(key) for key in ("issuer", "resource", "registration_endpoint")
    ):
        profile["registration_outcome"] = prior["registration_outcome"]


async def prepare(name: str, payload: dict) -> dict:
    previous = saved(name, payload.get("url"))
    defaults = previous["profile"] if previous else {}
    options = {
        **{key: defaults[key] for key in (OVERRIDES | {"issuer", "url"}) if key in defaults},
        **{key: value for key, value in payload.items() if value not in (None, "")},
    }
    options["url"] = hosted.https_url(options.get("url"))
    from . import mcp_servers

    mcp_servers.normalise_config(name, {"url": options["url"]})
    valid = (
        previous
        and previous["profile"].get("discovery_fingerprint")
        and time.time() - previous["updated_at"] < MAX_AGE
    )
    validated_at = time.time()
    if valid and not payload.get("refresh_metadata"):
        profile = apply(previous["profile"], options)
        validated_at = previous["updated_at"]
    else:
        try:
            profile = await hosted.discover(options)
            # Metadata refresh cannot erase a durable registration fence.
            # Only a genuinely different provider/client setup starts anew.
            retain_registration_fence(profile, previous)
        except HTTPException as error:
            # A temporary metadata outage must not erase a known valid setup.
            if (
                not previous
                or not previous["profile"].get("discovery_fingerprint")
                or error.status_code not in {502, 503, 504}
            ):
                raise
            profile = apply(previous["profile"], options)
            validated_at = previous["updated_at"]
    save(name, profile, validated_at=validated_at)
    if not profile["client_id"] and profile.get("registration_outcome") in {
        "pending",
        "uncertain",
        "rejected",
    }:
        raise HTTPException(
            400,
            {
                "code": "oauth_client_required",
                "message": "Provider registration was not confirmed. "
                "Enter an approved public client ID in Advanced settings.",
                "fields": ["client_id"],
            },
        )
    if not profile["client_id"] and not profile.get("registration_endpoint"):
        raise HTTPException(
            400,
            {
                "code": "oauth_client_required",
                "message": "This provider needs a registered public client ID. "
                "Enter it once in Advanced settings.",
                "fields": ["client_id"],
            },
        )
    return {"transport": "http", **profile, "share_account": True}


async def ready(name: str, payload: dict) -> dict:
    cached = saved(name, payload.get("url"))
    if (
        cached
        and time.time() - cached["updated_at"] < MAX_AGE
        and cached["profile"].get("discovery_fingerprint") == payload.get("discovery_fingerprint")
    ):
        # The actual receiver port differs from a port-0 template. Preserve it
        # for code exchange without changing the remembered callback template.
        return apply(cached["profile"], payload)
    profile = await hosted.discover(payload)  # Compatibility with older desktop clients.
    retain_registration_fence(profile, cached)
    if payload.get("discovery_fingerprint") != profile["discovery_fingerprint"]:
        raise HTTPException(409, "OAuth metadata changed; connect again to refresh setup automatically")
    template = payload.get("redirect_uri_template", profile["redirect_uri"])
    save(name, {**profile, "redirect_uri": template})
    return profile


async def register(name: str, profile: dict) -> dict:
    if not profile.get("registration_endpoint") or profile.get("registration_outcome") in {
        "pending",
        "uncertain",
        "rejected",
    }:
        raise HTTPException(400, "Provider needs a registered public OAuth client ID")
    candidate = {**profile, "registration_outcome": "pending"}
    save(name, candidate)  # Fence a lost registration response; never blindly register twice.
    metadata = {
        "client_name": NAME,
        "redirect_uris": [profile["redirect_uri"]],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    try:
        status, _, response = await hosted.fetch(profile["registration_endpoint"], json_body=metadata)
    except HTTPException:
        save(name, {**candidate, "registration_outcome": "uncertain"})
        raise HTTPException(
            502, "OAuth client registration outcome is uncertain; it will not be replayed"
        ) from None
    client = response.get("client_id")
    valid = (
        status in {200, 201}
        and isinstance(client, str)
        and 1 <= len(client) <= 512
        and client.isascii()
        and not any(ord(c) < 33 for c in client)
        and response.get("token_endpoint_auth_method", "none") == "none"
        and isinstance(response.get("redirect_uris"), list)
        and profile["redirect_uri"] in response["redirect_uris"]
    )
    if not valid:
        save(name, {**candidate, "registration_outcome": "rejected"})
        raise HTTPException(400, "Provider did not register a supported public client; use Advanced settings")
    # Ignore any returned secret; public-client token exchange must not use one.
    result = {
        **profile,
        "client_id": client,
        "redirect_uri_template": profile["redirect_uri"],
        "client_registration_verified": True,
        "registration_outcome": "complete",
    }
    save(name, {**result, "redirect_uri": result["redirect_uri_template"]})
    return result


def forget(name: str):
    db.execute("DELETE FROM mcp_oauth_setups WHERE name=?", (name,))
