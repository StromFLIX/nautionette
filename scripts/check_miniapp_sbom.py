"""Fail if the real compiler image's SPDX inventory silently omits locked crates.

Syft's image defaults do not catalog Cargo.lock. CI adds that cataloger without
removing the normal OS/binary ones. This checks the actual image SBOM, not a
separate source-only scan. The lock includes optional/vendored source packages;
it is not a claim that every optional dependency is linked into each user app.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path


def verify(sbom: dict, lock: dict) -> int:
    expected = {(p["name"], p["version"]) for p in lock["package"] if "source" in p}
    actual = {
        (p["name"], p.get("versionInfo", ""))
        for p in sbom["packages"]
        if p.get("sourceInfo", "").endswith("/sdk/template/Cargo.lock")
    }
    if not expected:
        raise ValueError("Miniapp Cargo lock contains no external crates")
    missing = expected - actual
    if missing:
        raise ValueError(f"Compiler image SBOM is missing locked crates: {sorted(missing)}")
    return len(expected)


def main() -> None:
    lock = tomllib.loads(Path("services/miniapp-sdk/Cargo.lock").read_text())
    count = verify(json.loads(Path(sys.argv[1]).read_text()), lock)
    print(f"Compiler image SBOM inventories all {count} external locked crates, including bundled SQLite")


if __name__ == "__main__":
    main()
