"""Source fingerprints shared by the broker and CI (stdlib only).

Keep this algorithm stable: existing installations already use these tags.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def context_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
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


if __name__ == "__main__":
    # CI runs from the repository root. No Docker/Python workspace install needed.
    root = Path("images")
    print(f"base={base_hash(root)}")
    print(f"default={agent_set_hash(root, 'default')}")
