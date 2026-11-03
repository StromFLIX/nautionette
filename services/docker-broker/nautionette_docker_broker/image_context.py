"""Source fingerprints shared by the broker and CI (stdlib only).

Keep the hashing algorithm stable: existing installations already use these tags.
The checked-in lock carries canonical source hashes into deployment systems that
inject build arguments into Dockerfiles before building/copying the contexts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

BUNDLED_LOCK = Path("/etc/nautionette/image-fingerprints.json")
SOURCE_LOCK = Path("images/source-fingerprints.json")


def context_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and not {"target", "__pycache__"}.intersection(p.relative_to(root).parts)
        and p.suffix not in {".pyc", ".pyo"}
    ):
        digest.update(str(file.relative_to(root)).encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:12]


def base_hash(root: Path) -> str:
    return context_hash(root / "pi-base")


def agent_set_hash(root: Path, agent_set: str) -> str:
    directory = root / "agent-sets" / agent_set
    if not directory.is_dir():
        return "missing"
    return hashlib.sha256((context_hash(directory) + base_hash(root)).encode()).hexdigest()[:12]


def source_fingerprints(root: Path, sdk: Path) -> dict:
    return {
        "schema": 1,
        "base": base_hash(root),
        "agent_sets": {
            p.name: agent_set_hash(root, p.name)
            for p in sorted((root / "agent-sets").iterdir())
            if p.is_dir() and (p / "Dockerfile").is_file()
        },
        "miniapp": context_hash(sdk),
    }


def bundled_hash(kind: str, agent_set: str | None = None) -> str | None:
    """Use deployment-owned, CI-checked identity, never transformed Dockerfiles.

    Source/local tests without a packaged lock retain dynamic context hashing.
    An invalid packaged lock fails closed instead of silently pulling other tags.
    """
    try:
        value = json.loads(BUNDLED_LOCK.read_text())
    except FileNotFoundError:
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or set(value) != {"schema", "base", "agent_sets", "miniapp"}
        or not isinstance(value["agent_sets"], dict)
        or any(
            not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{12}", v)
            for v in [value["base"], value["miniapp"], *value["agent_sets"].values()]
        )
    ):
        raise ValueError("Invalid bundled image fingerprint lock")
    if kind == "agent_sets":
        return value["agent_sets"].get(agent_set)
    return value[kind]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write-lock", action="store_true", help="Regenerate canonical source fingerprints")
    mode.add_argument("--check-lock", action="store_true", help="Fail if the checked-in lock is stale")
    args = parser.parse_args()
    # CI runs from the repository root. No Docker/workspace installation needed.
    hashes = source_fingerprints(Path("images"), Path("services/miniapp-sdk"))
    if args.write_lock:
        SOURCE_LOCK.write_text(json.dumps(hashes, indent=2) + "\n")
    elif args.check_lock:
        if json.loads(SOURCE_LOCK.read_text()) != hashes:
            parser.exit(1, "Image fingerprints are stale. Run this script with --write-lock.\n")
    else:
        print(f"base={hashes['base']}")
        print(f"default={hashes['agent_sets']['default']}")
        print(f"miniapp={hashes['miniapp']}")
