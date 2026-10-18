"""Pinned ArtCraft CLI installation. Used at image build and by the fixed broker installer.

No shell, package scripts or desktop binaries. Keep upstream documentation/licenses.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

CATALOG = json.loads(Path(__file__).with_name("catalog.json").read_text())
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXTRACTED = 512 * 1024 * 1024
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-[a-zA-Z0-9][a-zA-Z0-9.-]{0,40})?")


def architecture() -> str:
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine(), platform.machine())
    if arch not in {"x86_64", "aarch64"}:
        raise ValueError("ArtCraft requires Linux x86_64 or aarch64")
    return arch


def validate(spec: Any) -> dict[str, str]:
    if not isinstance(spec, dict) or set(spec) != {"app", "version", "arch", "sha256"}:
        raise ValueError("Invalid ArtCraft release specification")
    if (
        not isinstance(spec["app"], str)
        or spec["app"] not in CATALOG
        or not isinstance(spec["version"], str)
        or not VERSION.fullmatch(spec["version"])
    ):
        raise ValueError("Unknown app or invalid pinned version")
    if (
        spec["arch"] != architecture()
        or not isinstance(spec["sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", spec["sha256"])
    ):
        raise ValueError("Invalid architecture or release checksum")
    return dict(spec)


def asset_url(spec: dict[str, str]) -> str:
    app, version, arch = (spec[key] for key in ("app", "version", "arch"))
    suffix = "-cli" if CATALOG[app].get("cli_archive") else ""
    return f"https://github.com/storytold/{app}/releases/download/v{version}/{app}{suffix}-{version}-linux-{arch}.tar.gz"


def download(spec: dict[str, str], target: Path) -> None:
    # Initial URL is fixed to an allowlisted public repository; GitHub redirects
    # to its asset CDN. The pinned digest, not a mutable URL, identifies the bytes.
    for attempt in range(3):
        try:
            digest, size = hashlib.sha256(), 0
            request = urllib.request.Request(  # noqa: S310 - fixed HTTPS GitHub URL
                asset_url(spec), headers={"User-Agent": "Nautionette-ArtCraft"}
            )
            with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as out:  # noqa: S310 - fixed HTTPS GitHub URL
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARCHIVE:
                        raise ValueError("Release archive exceeds 256 MiB")
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != spec["sha256"]:
                raise ValueError("Release checksum mismatch")
            return
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def extract(archive: Path, target: Path, app: str) -> None:
    total, found, count = 0, False, 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            count += 1
            path = PurePosixPath(member.name)
            if count > 20_000 or path.is_absolute() or ".." in path.parts or "\\" in member.name:
                raise ValueError("Unsafe release archive path or member count")
            binary = path.name == f"{app}-cli"
            # All release docs (including font licenses/attributions) are kept.
            docs = (
                "doc" in path.parts
                or (len(path.parts) == 2 and path.suffix in {".md", ".txt"})
                or path.name.startswith(("LICENSE", "NOTICE", "OFL-"))
            )
            if not binary and not docs:
                continue
            if member.isdir():
                continue
            if not member.isfile() or member.size < 0:
                raise ValueError("Release files must be regular files, not links/devices")
            total += member.size
            if total > MAX_EXTRACTED:
                raise ValueError("Extracted CLI and documentation exceed 512 MiB")
            if binary:
                if found:
                    raise ValueError("Duplicate CLI in release")
                found = True
                dest = target / "bin" / f"{app}-cli"
            else:
                dest = target / "licenses" / Path(*path.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, dest.open("xb") as out:
                shutil.copyfileobj(source, out)
            dest.chmod(0o755 if binary else 0o644)
    if not found:
        raise ValueError("Release contains no native CLI")


def probe(target: Path, app: str) -> None:
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "nautionette-check", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    with tempfile.TemporaryDirectory() as work:
        # Bound stdout on disk rather than accumulating arbitrary native output
        # in the installer process. Docker also bounds the helper's resources.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(  # noqa: S603 - allowlisted installed CLI, no shell
                [str(target / "bin" / f"{app}-cli"), "mcp", *CATALOG[app].get("mcp_args", [])],
                input="".join(json.dumps(item) + "\n" for item in messages).encode(),
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=45,
                cwd=work,
                env={"PATH": os.environ.get("PATH", ""), "HOME": work, "XDG_CACHE_HOME": work},
            )
            if result.returncode or output.tell() > 8 * 1024 * 1024:
                raise ValueError("Native CLI failed its headless MCP compatibility check")
            output.seek(0)
            replies = [json.loads(line) for line in output if line.strip()]
    initialized = next((item.get("result", {}) for item in replies if item.get("id") == 1), {})
    tools = next((item.get("result", {}).get("tools", []) for item in replies if item.get("id") == 2), [])
    if (
        not initialized.get("protocolVersion")
        or not tools
        or not all(
            isinstance(tool.get("name"), str) and isinstance(tool.get("inputSchema"), dict) for tool in tools
        )
    ):
        raise ValueError("Native CLI returned an invalid MCP tool catalog")


def install(spec: dict[str, str], target: Path) -> dict[str, str]:
    spec = validate(spec)
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise ValueError("Installation target must be empty")
    with tempfile.TemporaryDirectory() as work:
        archive = Path(work) / "release.tar.gz"
        download(spec, archive)
        extract(archive, target, spec["app"])
    probe(target, spec["app"])
    (target / "manifest.json").write_text(json.dumps(spec))
    return spec


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--bundle":
        for app, item in CATALOG.items():
            spec = {
                "app": app,
                "version": item["version"],
                "arch": architecture(),
                "sha256": item["sha256"][architecture()],
            }
            install(spec, Path("/opt/artcraft") / app)
            print(f"Verified {app} {item['version']}", flush=True)
    else:
        result = install(json.loads(os.environ["ARTCRAFT_RELEASE"]), Path("/artifact"))
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
