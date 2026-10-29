"""Publish the locally tested images, retrying transient registry failures only.

Pushing the same image/tag is idempotent; rebuilding or weakening the CI/deploy
health gate is not necessary when GHCR temporarily rejects a layer/manifest.
Authentication stays with docker/login-action and Docker's credential store.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

RETRY_DELAYS = (10, 30)
TRANSIENT_ERRORS = (
    "unknown blob",
    "blob unknown",
    "blob_unknown",
    "unexpected eof",
    "connection reset",
    "connection refused",
    "i/o timeout",
    "tls handshake timeout",
    "context deadline exceeded",
    "too many requests",
    "toomanyrequests",
)


def transient_failure(output: str) -> bool:
    text = output.lower()
    if any(error in text for error in ("unauthorized", "authentication required", "denied", "forbidden")):
        return False
    return any(error in text for error in TRANSIENT_ERRORS) or bool(
        re.search(r"\b(?:429|500|502|503|504)\b", text)
    )


def push_image(image: str) -> None:
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            result = subprocess.run(  # noqa: S603 - fixed Docker command, no shell
                ["/usr/bin/docker", "push", image],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=600,
            )
            print(result.stdout, end="", flush=True)
            if result.returncode == 0:
                return
            retry = transient_failure(result.stdout)
        except subprocess.TimeoutExpired:
            print(f"Image push timed out: {image}", flush=True)
            retry = True
        if not retry or attempt == len(RETRY_DELAYS):
            raise RuntimeError(f"Could not publish {image}; deployment remains blocked")
        delay = RETRY_DELAYS[attempt]
        print(f"Transient registry failure for {image}; retrying in {delay}s.", flush=True)
        time.sleep(delay)


def main() -> None:
    repository = os.environ.get("REPOSITORY", "").lower()
    hashes = [os.environ.get(name, "") for name in ("BASE_HASH", "DEFAULT_HASH")]
    if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", repository) or any(
        not re.fullmatch(r"[0-9a-f]{12}", value) for value in hashes
    ):
        raise ValueError("Expected a repository and exact image source fingerprints")
    for name, fingerprint in zip(("base", "agent-default"), hashes, strict=True):
        image = f"ghcr.io/{repository}/pi-{name}:{fingerprint}"
        subprocess.run(  # noqa: S603 - fixed Docker command, no shell
            ["/usr/bin/docker", "tag", f"nautionette/pi-{name}:dev", image], check=True
        )
        push_image(image)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Image publication failed: {exc}", file=sys.stderr)
        sys.exit(1)
