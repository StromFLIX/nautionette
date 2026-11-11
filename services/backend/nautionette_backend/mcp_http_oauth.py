"""Direct HTTPS MCP OAuth: backend PKCE, sealed credentials, gateway bearer auth.

All mutations run under mcp_oauth's shared lock. Single-use authorization codes
and potentially rotating refresh tokens are fenced before network I/O. No token,
verifier, provider body or callback URL is returned to the desktop/agent tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import math
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from fastapi import HTTPException
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
)
from mcp.shared.auth_utils import check_resource_allowed

from . import mcp_servers
from .clients import gateway
from .db import db
from .events import bus
from .gateway_config import require_writable, resource_map
from .miniapp_capabilities import public_addresses
from .pi_package_secrets import _cipher, seal, unseal
from .runtime import forget_catalog

WORKIQ_URL = "https://workiq.svc.cloud.microsoft/mcp"
WORKIQ_CLIENT = "ba081686-5d24-4bc6-a0d6-d034ecffed87"
WORKIQ_REDIRECT = "http://localhost:12798"
TTL = 600
LIMIT = 65536
NAMESPACE = "mcp-oauth"
_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*")
_sessions: dict[str, Login] = {}
logger = logging.getLogger(__name__)


def https_url(value) -> str:
    try:
        parsed = urlsplit(value)
        if (
            not isinstance(value, str)
            or len(value) > 4096
            or parsed.scheme != "https"
            or not parsed.hostname
            or (parsed.port not in (None, 443) and not 1024 <= parsed.port <= 65535)
            or parsed.username
            or parsed.password
            or parsed.fragment
            or "\\" in value
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            400, "Hosted OAuth requires public HTTPS on 443 or an unprivileged TLS port"
        ) from exc
    return value


def redirect_uri(value, *, ephemeral=False) -> str:
    try:
        parsed = urlsplit(value)
        if (
            not isinstance(value, str)
            or len(value) > 512
            or parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.port is None
            or not (1024 <= parsed.port <= 65535 or ephemeral and parsed.port == 0)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or "\\" in value
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            400, "Use an HTTP loopback redirect with an unprivileged port; port 0 is a template only"
        ) from exc
    return value


def string_list(value, label) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) > 128
        or any(not isinstance(v, str) or len(v) > 2048 for v in value)
    ):
        raise HTTPException(400, f"Invalid OAuth {label}")
    return value


def microsoft_issuer(issuer: str) -> bool:
    parsed = urlsplit(issuer)
    return parsed.hostname == "login.microsoftonline.com" and bool(
        re.fullmatch(r"/[A-Za-z0-9.-]+/v2\.0", parsed.path)
    )


async def public_url(url):
    https_url(url)
    try:
        addresses = await asyncio.to_thread(public_addresses, urlsplit(url).hostname)
        # Docker hosts often have no IPv6 route. Pin a validated IPv4 address
        # first, rather than lexically choosing an unreachable IPv6 address.
        return sorted(addresses, key=lambda address: ipaddress.ip_address(address).version)
    except OSError as exc:
        raise HTTPException(502, "OAuth provider DNS could not be resolved") from exc


def network_client():
    return httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(20, connect=4))


async def fetch(
    url: str, data: dict | None = None, *, json_body: dict | None = None
) -> tuple[int, dict, dict]:
    """Bounded, pinned public HTTPS with original TLS/SNI and no ambient auth/proxy."""
    parsed = urlsplit(https_url(url))
    try:
        async with asyncio.timeout(25):
            addresses = await public_url(url)
            mutation = data is not None or json_body is not None
            for index, address in enumerate(addresses[:1] if mutation else addresses):
                for attempt in range(1 if mutation else 2):
                    try:
                        result = await request_at(parsed, address, data, json_body)
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        if mutation or index == len(addresses) - 1 and attempt == 1:
                            raise
                        continue
                    if not mutation and result[0] in {502, 503, 504} and attempt == 0:
                        await asyncio.sleep(0.1)
                        continue
                    return result
            raise HTTPException(502, "OAuth metadata could not be reached")
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        raise HTTPException(
            502,
            "OAuth request outcome is unavailable; an authorization code or refresh token "
            "will not be replayed",
        ) from exc


async def request_at(parsed, address, data, json_body):
    address = f"[{address}]" if ":" in address else address
    authority = address + (f":{parsed.port}" if parsed.port not in (None, 443) else "")
    path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
    async with network_client() as client:
        async with client.stream(
            "POST" if data is not None or json_body is not None else "GET",
            f"https://{authority}{path}",
            data=data,
            json=json_body,
            headers={"Host": parsed.netloc, "Accept": "application/json"},
            extensions={"sni_hostname": parsed.hostname},
        ) as response:
            body = bytearray()
            async for block in response.aiter_bytes():
                body.extend(block)
                if len(body) > LIMIT:
                    raise HTTPException(502, "OAuth provider response exceeded 64 KiB")
            try:
                value = json.loads(body)
            except ValueError:
                value = {}
            return response.status_code, dict(response.headers), value if isinstance(value, dict) else {}


async def discover(payload: dict) -> dict:
    url = https_url(payload.get("url"))
    if urlsplit(url).query:
        raise HTTPException(400, "Use the MCP resource URL without credentials or query parameters")
    metadata = None
    urls = build_protected_resource_metadata_discovery_urls(None, url)
    for endpoint in urls:
        status, _, value = await fetch(endpoint)
        if (
            status == 200
            and isinstance(value.get("resource"), str)
            and check_resource_allowed(requested_resource=url, configured_resource=value["resource"])
        ):
            metadata = value
            break
    if metadata is None:
        # Some servers advertise a nonstandard metadata location in their challenge.
        _, headers, _ = await fetch(url)
        match = re.search(r'resource_metadata="([^"]+)"', headers.get("www-authenticate", ""))
        if match:
            status, _, value = await fetch(https_url(match[1]))
            if (
                status == 200
                and isinstance(value.get("resource"), str)
                and check_resource_allowed(requested_resource=url, configured_resource=value["resource"])
            ):
                metadata = value
    if metadata is None:
        raise HTTPException(400, "MCP server did not advertise matching OAuth protected-resource metadata")
    servers = string_list(metadata.get("authorization_servers", []), "authorization servers")
    if not servers:
        raise HTTPException(400, "MCP server did not advertise an authorization server")
    issuer = payload.get("issuer") or servers[0]
    if issuer not in servers:
        raise HTTPException(400, "Selected issuer is not advertised by the MCP server")
    https_url(issuer)
    auth = None
    for endpoint in build_oauth_authorization_server_metadata_discovery_urls(issuer, url):
        status, _, value = await fetch(endpoint)
        if status == 200 and value.get("authorization_endpoint") and value.get("token_endpoint"):
            auth = value
            break
    if auth is None:
        raise HTTPException(400, "Authorization server did not expose supported OAuth metadata")
    microsoft = microsoft_issuer(issuer)
    if auth.get("issuer") != issuer and not (
        microsoft and auth.get("issuer") == "https://login.microsoftonline.com/{tenantid}/v2.0"
    ):
        raise HTTPException(400, "OAuth metadata issuer does not match the advertised server")
    if "code" not in string_list(auth.get("response_types_supported", []), "response types"):
        raise HTTPException(400, "Provider does not support authorization code login")
    if "code_challenge_methods_supported" in auth and "S256" not in string_list(
        auth["code_challenge_methods_supported"], "PKCE methods"
    ):
        raise HTTPException(400, "Provider does not support PKCE S256")
    # Entra supports public clients although OIDC metadata lists only confidential
    # auth methods. This compatibility exception is limited to Microsoft v2 issuers.
    methods = string_list(
        auth.get("token_endpoint_auth_methods_supported", ["client_secret_basic"]), "client auth methods"
    )
    if not microsoft and "none" not in methods:
        raise HTTPException(
            400, "This bridge needs a registered public OAuth client; confidential clients are not supported"
        )
    for key in ["authorization_endpoint", "token_endpoint"]:
        await public_url(auth[key])
        if microsoft and urlsplit(auth[key]).hostname != "login.microsoftonline.com":
            raise HTTPException(400, "Unexpected Microsoft authorization endpoint")
    if set(parse_qs(urlsplit(auth["authorization_endpoint"]).query)) & {
        "client_id",
        "state",
        "redirect_uri",
        "scope",
        "resource",
        "response_type",
        "code_challenge",
    }:
        raise HTTPException(400, "OAuth endpoint contains conflicting authorization parameters")
    registration = auth.get("registration_endpoint")
    if registration:
        await public_url(registration)
    client_id = payload.get("client_id") or (WORKIQ_CLIENT if url == WORKIQ_URL else "")
    if not isinstance(client_id, str) or len(client_id) > 512 or any(ord(c) < 33 for c in client_id):
        raise HTTPException(400, "Invalid public client ID")
    scope = payload.get("scope") or " ".join(string_list(metadata.get("scopes_supported", []), "scopes"))
    if not isinstance(scope, str) or len(scope) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in scope):
        raise HTTPException(400, "Invalid OAuth scope")
    supported_scopes = string_list(auth.get("scopes_supported", []), "authorization scopes")
    if microsoft or "offline_access" in supported_scopes:
        scope = " ".join(dict.fromkeys([*scope.split(), "offline_access"]))
    if microsoft:
        scope = " ".join(dict.fromkeys([*scope.split(), "openid"]))
    redirect = payload.get("redirect_uri") or (
        WORKIQ_REDIRECT if url == WORKIQ_URL else "http://127.0.0.1:0/callback"
    )
    redirect_uri(redirect, ephemeral=True)
    mode = payload.get("response_mode", "query")
    if mode not in {"query", "form_post"} or mode not in string_list(
        auth.get("response_modes_supported", ["query"]), "response modes"
    ):
        raise HTTPException(400, "Provider does not support the selected query/form_post response mode")
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "url": url,
                "resource": metadata["resource"],
                "issuer": issuer,
                "authorization_endpoint": auth["authorization_endpoint"],
                "token_endpoint": auth["token_endpoint"],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "url": url,
        "resource": metadata["resource"],
        "issuer": issuer,
        "authorization_endpoint": auth["authorization_endpoint"],
        "token_endpoint": auth["token_endpoint"],
        "client_id": client_id,
        "scope": scope,
        "redirect_uri": redirect,
        "response_mode": mode,
        "use_resource": not microsoft,
        "discovery_fingerprint": fingerprint,
        "client_registration_verified": False,
        "registration_endpoint": registration,
        "response_modes_supported": string_list(
            auth.get("response_modes_supported", ["query"]), "response modes"
        ),
    }


def ready_key():
    try:
        # Never mint a replacement key over credentials whose backup key is lost.
        existing = db.one("SELECT name FROM mcp_http_oauth WHERE secret!='' LIMIT 1")
        _cipher(create=existing is None, namespace=NAMESPACE)
    except (OSError, ValueError) as exc:
        raise HTTPException(
            503, "OAuth credential key is missing or invalid; restore the backend data backup"
        ) from exc


@dataclass
class Login:
    name: str
    profile: dict
    previous: dict | None
    verifier: str
    authorization: dict
    expires: float = field(default_factory=lambda: time.monotonic() + TTL)
    used: bool = False
    digest: str = ""
    tokens: dict | None = None
    revision: str = field(default_factory=lambda: secrets.token_hex(16))
    complete: bool = False
    failed: bool = False


async def start(name: str, payload: dict) -> dict:
    if not isinstance(payload.get("discovery_fingerprint"), str) or not re.fullmatch(
        r"[a-f0-9]{64}", payload["discovery_fingerprint"]
    ):
        raise HTTPException(409, "Discover the hosted server before starting OAuth")
    from . import mcp_oauth_setup

    profile = await mcp_oauth_setup.ready(name, payload)
    if payload.get("share_account") is not True:
        raise HTTPException(400, "Confirm connecting this account to the shared instance")
    profile["redirect_uri"] = redirect_uri(payload.get("redirect_uri"))
    template = redirect_uri(payload.get("redirect_uri_template", profile["redirect_uri"]), ephemeral=True)
    actual, wanted = urlsplit(profile["redirect_uri"]), urlsplit(template)
    if (
        actual.hostname != wanted.hostname
        or actual.path != wanted.path
        or wanted.port not in (0, actual.port)
    ):
        raise HTTPException(400, "Prepared callback does not match the redirect template")
    profile["redirect_uri_template"] = template
    mcp_servers.normalise_config(name, {"url": profile["url"]})
    mcp_servers.reachable_endpoint(profile["url"])
    mode, targets = await mcp_servers.fetch_resources()
    require_writable(mode)
    previous = resource_map(targets).get(name, {}).get("value")
    if previous is None:
        baseline = await gateway.config()
        if any(t["name"] == name for t in baseline.get("targets", [])):
            raise HTTPException(409, "That MCP name is defined in the gateway config")
    ready_key()
    if not profile["client_id"]:
        profile = await mcp_oauth_setup.register(name, profile)
    pkce = PKCEParameters.generate()
    state, identifier = secrets.token_urlsafe(32), "http-" + secrets.token_hex(32)
    params = {
        "client_id": profile["client_id"],
        "redirect_uri": profile["redirect_uri"],
        "state": state,
        "response_type": "code",
        "response_mode": profile["response_mode"],
        "scope": profile["scope"],
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
    }
    if profile["use_resource"]:
        params["resource"] = profile["resource"]
    separator = "&" if urlsplit(profile["authorization_endpoint"]).query else "?"
    authorization = {
        "id": secrets.token_hex(16),
        "authorization_url": profile["authorization_endpoint"] + separator + urlencode(params),
        "state": state,
        "redirect_uri": profile["redirect_uri"],
        "port": actual.port,
        "response_mode": profile["response_mode"],
    }
    _sessions[identifier] = Login(name, profile, previous, pkce.code_verifier, authorization)
    return {"id": identifier, "status": "authorizing", "expires_in": TTL}


def session(identifier: str) -> Login:
    login = _sessions.get(identifier)
    if not login or login.expires <= time.monotonic():
        raise HTTPException(410, "Hosted OAuth login expired or backend restarted; sign in again")
    return login


async def exchange(profile: dict, data: dict, previous: dict | None = None) -> dict:
    status, _, value = await fetch(profile["token_endpoint"], data)
    if status != 200 or value.get("error"):
        error = value.get("error")
        allowed = {
            "invalid_grant",
            "invalid_client",
            "unauthorized_client",
            "invalid_scope",
            "access_denied",
            "interaction_required",
        }
        label = error if isinstance(error, str) and error in allowed else "provider_refused"
        raise HTTPException(
            400, f"OAuth token request failed ({label}); check provider/client permissions and sign in again"
        )
    token, refresh = (
        value.get("access_token"),
        value.get("refresh_token", (previous or {}).get("refresh_token", "")),
    )
    expiry, kind = value.get("expires_in"), value.get("token_type")
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= 32768
        or not _TOKEN.fullmatch(token)
        or not isinstance(refresh, str)
        or len(refresh) > 32768
        or "\0" in refresh
        or not isinstance(kind, str)
        or kind.lower() != "bearer"
        or isinstance(expiry, bool)
        or not isinstance(expiry, (int, float))
        or not math.isfinite(expiry)
        or not 1 <= expiry <= 31536000
    ):
        raise HTTPException(502, "Provider returned invalid bearer credentials or expiry")
    now = time.time()
    return {
        "access_token": token,
        "refresh_token": refresh,
        "expires_at": now + expiry,
        "renew_at": now + max(1, expiry - min(120, expiry * 0.2)),
    }


async def callback(identifier: str, payload: dict) -> dict:
    login = session(identifier)
    fields = payload.get("fields")
    if (
        set(payload) - {"capture_id", "method", "fields"}
        or not isinstance(fields, dict)
        or len(fields) > 32
        or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 16384 for k, v in fields.items())
        or any(k in fields for k in ["access_token", "refresh_token", "id_token"])
        or len(json.dumps(payload).encode()) > LIMIT
    ):
        raise HTTPException(400, "Invalid hosted OAuth callback")
    expected_method = "POST" if login.profile["response_mode"] == "form_post" else "GET"
    if (
        payload.get("capture_id") != login.authorization["id"]
        or payload.get("method") != expected_method
        or not isinstance(fields.get("state"), str)
        or not fields["state"].isascii()
        or not secrets.compare_digest(fields["state"], login.authorization["state"])
        or bool(fields.get("code")) == bool(fields.get("error"))
    ):
        raise HTTPException(400, "OAuth callback capture, state or response mode did not match")
    issuer = fields.get("iss")
    if (
        issuer
        and issuer != login.profile["issuer"]
        and not (microsoft_issuer(login.profile["issuer"]) and microsoft_issuer(issuer))
    ):
        raise HTTPException(400, "Authorization response issuer did not match")
    digest = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
    if login.used:
        if digest != login.digest:
            raise HTTPException(409, "Callback was already submitted with different fields")
        if login.failed:
            raise HTTPException(410, "Code exchange failed or is uncertain; start a new login, not a replay")
        return {"status": "forwarded"}
    login.used, login.digest = True, digest
    if fields.get("error"):
        login.failed, login.verifier = True, ""
        raise HTTPException(400, "Provider refused or cancelled OAuth sign-in")
    data = {
        "grant_type": "authorization_code",
        "client_id": login.profile["client_id"],
        "redirect_uri": login.profile["redirect_uri"],
        "code": fields["code"],
        "code_verifier": login.verifier,
    }
    if login.profile["use_resource"]:
        data["resource"] = login.profile["resource"]
    try:
        login.tokens = await exchange(login.profile, data)
    except HTTPException:
        login.failed = True
        raise
    finally:
        login.verifier = ""
    return {"status": "forwarded"}


def public_profile(profile: dict) -> dict:
    return {
        "mode": "https",
        **{key: profile[key] for key in ["url", "issuer", "client_id", "scope", "response_mode"]},
        "redirect_uri": profile["redirect_uri_template"],
    }


def store(login: Login, tool_names: list[str], status="gateway_pending"):
    prior = db.one("SELECT * FROM mcp_http_oauth WHERE name=?", (login.name,))
    backup = prior
    if prior and prior["revision"] == login.revision:
        backup = unseal(prior["secret"], namespace=NAMESPACE).get("backup")
    value = {
        "profile": login.profile,
        "tokens": login.tokens,
        "previous": login.previous,
        "operation": "connect",
        "backup": backup,
        "tool_names": tool_names,
    }
    db.execute(
        "INSERT INTO mcp_http_oauth VALUES (?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
        "profile=excluded.profile,secret=excluded.secret,status=excluded.status,expires_at=excluded.expires_at,"
        "retry_at=excluded.retry_at,revision=excluded.revision",
        (
            login.name,
            json.dumps({**public_profile(login.profile), "refreshable": bool(login.tokens["refresh_token"])}),
            seal(value, namespace=NAMESPACE),
            status,
            login.tokens["expires_at"],
            time.time(),
            login.revision,
        ),
    )


async def current_target(name: str) -> dict | None:
    _, targets = await mcp_servers.fetch_resources()
    return resource_map(targets).get(name, {}).get("value")


def target(name, value):
    return mcp_servers.target_value(name, value["profile"]["url"], value["tokens"]["access_token"])


async def activate(row, value) -> bool:
    # Config-store acknowledgement/readback is not data-plane readiness. A
    # fresh federated MCP session must initialize and list this target's tools
    # before held calls are released. Never call a business tool here or retry
    # an OAuth exchange to check readiness.
    if value["tokens"]["expires_at"] <= time.time():
        return False
    try:
        if await current_target(row["name"]) != target(row["name"], value):
            return False
        names = value.get("tool_names")
        if names is None:  # Pending credentials written by an older backend.
            verdict = await mcp_servers.probe_http_gateway(
                row["name"], value["profile"]["url"], value["tokens"]["access_token"]
            )
            if not verdict["ok"]:
                return False
            names = value["tool_names"] = verdict["tool_names"]
        tools = await gateway.mcp_tools(timeout=10)
        if not names or not set(names).issubset(tool["name"] for tool in tools):
            return False
    except (HTTPException, httpx.HTTPError, ValueError, TimeoutError):
        return False  # Leave gateway_pending; poll/recovery may check again.
    value.pop("backup", None)
    value["previous"] = None
    db.execute(
        "UPDATE mcp_http_oauth SET secret=?,status='connected',retry_at=? WHERE name=? AND revision=?",
        (seal(value, namespace=NAMESPACE), value["tokens"]["renew_at"], row["name"], row["revision"]),
    )
    for login in _sessions.values():
        if login.name == row["name"] and login.revision == row["revision"]:
            login.complete, login.tokens, login.previous = True, None, None
    return True


def updated_target(name, previous, expected):
    # A managed refresh is not an external edit. Keep reconnect snapshots current.
    from . import mcp_oauth

    for login in mcp_oauth._sessions.values():
        if login.name == name and not login.complete and login.previous == previous:
            login.previous = expected
    for login in _sessions.values():
        if login.name == name and not login.complete and login.previous == previous:
            login.previous = expected


def discard(row):
    db.execute("DELETE FROM mcp_http_oauth WHERE name=? AND revision=?", (row["name"], row["revision"]))


def restore(row, value):
    backup = value.get("backup")
    if backup:
        db.execute(
            "UPDATE mcp_http_oauth SET profile=?,secret=?,status=?,expires_at=?,retry_at=?,revision=? "
            "WHERE name=? AND revision=?",
            (
                backup["profile"],
                backup["secret"],
                backup["status"],
                backup["expires_at"],
                backup["retry_at"],
                backup["revision"],
                row["name"],
                row["revision"],
            ),
        )
    else:
        discard(row)


async def poll(identifier: str) -> dict:
    login = session(identifier)
    if login.complete:
        return {"status": "complete", "message": "HTTPS OAuth connected and MCP handshake passed."}
    if login.failed:
        return {"status": "failed", "message": "Hosted OAuth failed; start a fresh sign-in."}
    if not login.tokens:
        return {
            "status": "authorizing",
            "message": "Sign in in your browser.",
            "authorization": login.authorization,
        }
    if login.tokens["expires_at"] <= time.time():
        login.failed = True
        raise HTTPException(410, "Access token expired before connection; sign in again")
    expected = mcp_servers.target_value(login.name, login.profile["url"], login.tokens["access_token"])
    current = await current_target(login.name)
    if current != login.previous and current != expected:
        raise HTTPException(409, "MCP configuration changed during login; nothing was overwritten")
    # A read-only MCP handshake before exposing the connection to all chats.
    await public_url(login.profile["url"])
    verdict = await mcp_servers.probe_http_gateway(
        login.name, login.profile["url"], login.tokens["access_token"]
    )
    if not verdict["ok"]:
        raise HTTPException(400, verdict["message"])
    store(login, verdict["tool_names"])
    try:
        if current != expected:
            await gateway.put_config_resources("mcp.target", [expected])
    except httpx.HTTPError as exc:
        raise HTTPException(
            502, "Gateway update is uncertain; retry connection checking, not the OAuth code"
        ) from exc
    row = db.one("SELECT * FROM mcp_http_oauth WHERE name=? AND revision=?", (login.name, login.revision))
    if not await activate(row, unseal(row["secret"], namespace=NAMESPACE)):
        return {"status": "gateway_pending", "message": "Waiting for authenticated MCP federation readiness."}
    db.execute("DELETE FROM mcp_cli_oauth WHERE name=?", (login.name,))
    forget_catalog()
    bus.publish("mcp.server.changed", {"server": login.name, "configured": True})
    return {"status": "complete", "message": "HTTPS OAuth connected and MCP handshake passed."}


async def cancel(identifier: str):
    login = _sessions.pop(identifier, None)
    if not login or login.complete:
        return
    row = db.one("SELECT * FROM mcp_http_oauth WHERE name=? AND revision=?", (login.name, login.revision))
    if row:
        value = unseal(row["secret"], namespace=NAMESPACE)
        # Persist cancellation before reading a possibly unavailable gateway.
        value["cancel_until"] = time.time() + 60
        db.execute(
            "UPDATE mcp_http_oauth SET status='cancelled',secret=?,retry_at=0 WHERE name=? AND revision=?",
            (seal(value, namespace=NAMESPACE), login.name, login.revision),
        )
        if await current_target(login.name) == target(login.name, value):
            # A lost gateway reply may have actually completed the connection.
            await activate(row, value)
    login.tokens, login.verifier, login.previous = None, "", None


async def expire():
    for identifier in list(_sessions):
        if _sessions[identifier].expires <= time.monotonic():
            try:
                await cancel(identifier)
            except HTTPException:
                logger.warning("HTTPS OAuth cleanup needs attention; no credentials logged")


def forget(name):
    for identifier in list(_sessions):
        if _sessions[identifier].name == name:
            login = _sessions.pop(identifier)
            login.tokens, login.verifier, login.previous = None, "", None
    db.execute("DELETE FROM mcp_http_oauth WHERE name=?", (name,))


def public_connections():
    result = {}
    for row in db.query("SELECT name,profile,status,expires_at,revision FROM mcp_http_oauth"):
        profile = json.loads(row["profile"])
        status = profile.pop("_problem", row["status"])
        result[row["name"]] = {
            **profile,
            "status": status,
            "expires_at": row["expires_at"],
            "revision": row["revision"],
        }
    return result


async def disable(row, value):
    current = await current_target(row["name"])
    if current == target(row["name"], value) or current is not None and current == value.get("previous"):
        await gateway.delete_config_resource("mcp.target", row["name"])
    db.execute(
        "UPDATE mcp_http_oauth SET secret='',status='reauth_required',retry_at=0 WHERE name=? AND revision=?",
        (row["name"], row["revision"]),
    )
    forget_catalog()
    bus.publish("mcp.oauth.reauth", {"server": row["name"]})


async def refresh_once():
    now = time.time()
    for row in db.query("SELECT * FROM mcp_http_oauth WHERE retry_at<=?", (now,)):
        if not row["secret"]:
            continue
        decoded = False
        try:
            value = unseal(row["secret"], namespace=NAMESPACE)
            decoded = True
            public = json.loads(row["profile"])
            if public.pop("_problem", None):
                db.execute(
                    "UPDATE mcp_http_oauth SET profile=? WHERE name=? AND revision=?",
                    (json.dumps(public), row["name"], row["revision"]),
                )
            name, tokens = row["name"], value["tokens"]
            current, expected = await current_target(name), target(name, value)
            if row["status"] == "cancelled":
                if current == expected:
                    value["cleanup_started"] = True
                    db.execute(
                        "UPDATE mcp_http_oauth SET secret=? WHERE name=? AND revision=?",
                        (seal(value, namespace=NAMESPACE), name, row["revision"]),
                    )
                    if value.get("previous") is None:
                        await gateway.delete_config_resource("mcp.target", name)
                    else:
                        await gateway.put_config_resources("mcp.target", [value["previous"]])
                    restore(row, value)
                elif current == value.get("previous"):
                    if not value.get("cleanup_started") and now < value["cancel_until"]:
                        continue  # Fence a still in-flight, lost gateway write.
                    restore(row, value)
                else:
                    discard(row)  # An external edit must not be undone by cleanup.
                continue
            if row["status"] in {"refreshing", "reauth_required"}:
                await disable(row, value)
                continue
            if row["status"] == "gateway_pending":
                if current == expected:
                    updated_target(name, value.get("previous"), expected)
                    await activate(row, value)
                elif current != value.get("previous"):
                    discard(row)  # External edit owns the target now; never overwrite it.
                elif value["operation"] == "refresh":
                    if tokens["expires_at"] <= now:
                        await disable(row, value)
                    else:
                        await gateway.put_config_resources("mcp.target", [expected])
                        updated_target(name, current, expected)
                        await activate(row, value)
                continue  # New connections are not published without an explicit poll.
            if current != expected:
                discard(row)
                continue
            if not tokens["refresh_token"]:
                if tokens["expires_at"] <= now:
                    await disable(row, value)
                else:
                    db.execute(
                        "UPDATE mcp_http_oauth SET retry_at=? WHERE name=?", (tokens["expires_at"], name)
                    )
                continue
            # Durable compare-and-set before consuming a potentially rotating
            # refresh token. A crash/lost result requires login, never replay.
            claimed = db.execute(
                "UPDATE mcp_http_oauth SET status='refreshing',retry_at=? "
                "WHERE name=? AND revision=? AND status='connected'",
                (now + 60, name, row["revision"]),
            ).rowcount
            if not claimed:
                continue
            data = {
                "grant_type": "refresh_token",
                "client_id": value["profile"]["client_id"],
                "refresh_token": tokens["refresh_token"],
            }
            if value["profile"]["use_resource"]:
                data["resource"] = value["profile"]["resource"]
            value["tokens"] = await exchange(value["profile"], data, tokens)
            value["previous"], value["operation"] = current, "refresh"
            # New rotated credentials are durable before touching the gateway.
            stored = db.execute(
                "UPDATE mcp_http_oauth SET secret=?,status='gateway_pending',expires_at=?,retry_at=0 "
                "WHERE name=? AND revision=? AND status='refreshing'",
                (seal(value, namespace=NAMESPACE), value["tokens"]["expires_at"], name, row["revision"]),
            ).rowcount
            if not stored:
                continue  # A concurrent disconnect revoked our ownership.
            desired = target(name, value)
            await gateway.put_config_resources("mcp.target", [desired])
            updated_target(name, current, desired)
            await activate(row, value)
        except (HTTPException, httpx.HTTPError):
            # Never log exception/provider bodies; they may include credentials.
            if not decoded:
                public = json.loads(row["profile"])
                public["_problem"] = "storage_unavailable"
                db.execute(
                    "UPDATE mcp_http_oauth SET profile=? WHERE name=? AND revision=?",
                    (json.dumps(public), row["name"], row["revision"]),
                )
            logger.warning("HTTPS MCP OAuth needs attention for %s; no credentials logged", row["name"])
            db.execute(
                "UPDATE mcp_http_oauth SET retry_at=? WHERE name=? AND revision=?",
                (now + 30, row["name"], row["revision"]),
            )
            bus.publish("mcp.oauth.problem", {"server": row["name"]})
