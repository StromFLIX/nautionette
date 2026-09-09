"""Data-only contracts shared by the backend and the fixed package installer verb."""

from __future__ import annotations

import re
from typing import Any

RESOURCE_TYPES = ("extensions", "skills", "prompts", "themes")
ID_PATTERN = r"[a-f0-9]{32}"
_NPM = re.compile(
    r"npm:(@?[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)?)(?:@([a-zA-Z0-9.*^~+_-][a-zA-Z0-9.*^~+_-]*))?"
)
_GITHUB = re.compile(
    r"(?:git:)?(?:https://)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:@([A-Za-z0-9_./-]+))?"
)
_RESERVED_ENV = {
    "HOME",
    "PATH",
    "SHELL",
    "USER",
    "LOGNAME",
    "PWD",
    "TMPDIR",
    "TMP",
    "TEMP",
    "BASH_ENV",
    "ENV",
    "IFS",
    "CDPATH",
    "SHELLOPTS",
    "BASHOPTS",
    "PYTHONPATH",
    "PYTHONHOME",
    "BACKEND_URL",
    "MCP_URL",
    "INTERNAL_TOKEN",
    "AGENTGATEWAY_URL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "CURL_CA_BUNDLE",
}


def installation_source(value: Any) -> dict[str, str]:
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError("Use npm:package[@version] or https://github.com/owner/repo[@ref]")
    match = _NPM.fullmatch(value.strip())
    if match:
        name, version = match.groups()
        if (name.startswith("@") and "/" not in name) or (not name.startswith("@") and "/" in name):
            raise ValueError("Invalid npm package name")
        return {"kind": "npm", "name": name, "spec": name + (f"@{version}" if version else "")}
    match = _GITHUB.fullmatch(value.strip())
    if match:
        owner, repo, ref = match.groups()
        if (
            owner in {".", ".."}
            or repo in {".", ".."}
            or (ref and (ref.startswith(("-", "/")) or ".." in ref or "//" in ref))
        ):
            raise ValueError("Invalid GitHub repository or ref")
        return {"kind": "git", "url": f"https://github.com/{owner}/{repo}.git", "ref": ref or "HEAD"}
    raise ValueError(
        "Only npm packages and public HTTPS GitHub repositories are supported; no credentials or local paths"
    )


def revision_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(ID_PATTERN, value):
        raise ValueError("Invalid package revision ID")
    return value


def filters(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict) or value.keys() - set(RESOURCE_TYPES):
        raise ValueError("Resource filters accept extensions, skills, prompts and themes")
    result = {}
    for kind, patterns in value.items():
        if not isinstance(patterns, list) or len(patterns) > 100:
            raise ValueError("Each resource filter must be a list of at most 100 patterns")
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern or len(pattern) > 300:
                raise ValueError("Resource patterns must be non-empty strings of at most 300 characters")
            path = pattern.lstrip("!+-")
            if not path or path.startswith(("/", "~")) or ".." in path.split("/") or "\\" in path:
                raise ValueError("Resource filters must stay inside the package")
        result[kind] = patterns
    return result


def environment_key(key: Any) -> bool:
    return bool(
        isinstance(key, str)
        and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", key)
        and key not in _RESERVED_ENV
        and not key.startswith(("PI_", "NODE_", "NPM_", "GIT_", "NAUTIONETTE_", "AGENT_", "LD_", "DYLD_"))
    )


def config_path(path: Any) -> bool:
    # Deliberately not a general file-write API: no executable resources, core Pi
    # settings, shell startup files or project paths can be replaced from Settings.
    return bool(
        isinstance(path, str)
        and re.fullmatch(r"agent/[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\.(?:json|yaml|yml|toml|txt)", path)
        and path not in {"agent/settings.json", "agent/auth.json", "agent/models.json", "agent/trust.json"}
    )


def configuration(value: Any, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() - {"env", "files"}:
        raise ValueError("Configuration accepts env and files objects")
    result = {}
    for kind, valid_key in (("env", environment_key), ("files", config_path)):
        values = value.get(kind, {})
        if not isinstance(values, dict) or len(values) > 50:
            raise ValueError(f"{kind} must contain at most 50 entries")
        result[kind] = {}
        for key, item in values.items():
            if not valid_key(key):
                raise ValueError(f"Unsupported {kind} key/path; runtime control settings are reserved")
            if item is None:
                item = (previous or {}).get(kind, {}).get(key)
            if not isinstance(item, str) or "\x00" in item:
                raise ValueError(
                    "Values must be strings; null keeps an existing value, omit a key to remove it"
                )
            result[kind][key] = item
    if sum(len(v.encode()) for entries in result.values() for v in entries.values()) > 64_000:
        raise ValueError("Package configuration must fit in 64 KB")
    return result
