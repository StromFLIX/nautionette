"""Summarize downloaded CI SPDX/Grype artifacts without discarding any finding.

Download the selected CI run's sbom-* and vulnerabilities-* artifacts into an
otherwise empty directory (one subdirectory per artifact), then run:

    python -m scripts.summarize_dependency_artifacts DIR --run-url URL --output FILE

This is evidence, not an allowlist or an exploitability assessment. It never
changes scanner exit policies. Original report hashes make the summary traceable;
multiple binaries/images may report the same advisory/package/version tuple.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def summarize(root: Path, run_url: str) -> dict:
    artifacts, inventories, findings = [], {}, defaultdict(set)
    for path in sorted(root.glob("*/*.json")):
        kind = path.parent.name
        if not kind.startswith(("sbom-", "vulnerabilities-")):
            continue
        raw = path.read_bytes()
        report = json.loads(raw)
        artifacts.append({"artifact": kind, "file": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
        if kind.startswith("sbom-"):
            # Hash the complete inventory, including unowned/nested toolchains;
            # original SPDX remains authoritative rather than duplicating it.
            packages = sorted(
                (package["name"], package.get("versionInfo", "")) for package in report["packages"]
            )
            inventories[kind] = {
                "package_count": len(packages),
                "packages_sha256": hashlib.sha256(json.dumps(packages).encode()).hexdigest(),
            }
            continue
        for match in report["matches"]:
            package, advisory = match["artifact"], match["vulnerability"]
            fix = advisory.get("fix", {})
            key = (
                package["name"],
                package["version"],
                package.get("type", ""),
                advisory["id"],
                advisory["severity"],
                advisory.get("namespace", ""),
                fix.get("state", ""),
                tuple(sorted(fix.get("versions", []))),
            )
            findings[key].add(f"{kind}/{path.name}")
    if not inventories or not findings:
        raise ValueError(
            "Expected both SPDX inventories and nonempty Grype findings; check artifact downloads"
        )
    return {
        "run_url": run_url,
        "scope": (
            "Observed CI images only; excludes deployments, other architectures and native artifact volumes"
        ),
        "artifacts": artifacts,
        "inventories": inventories,
        "findings": [
            {
                "package": key[0],
                "version": key[1],
                "type": key[2],
                "advisory": key[3],
                "severity": key[4],
                "namespace": key[5],
                "fix_state": key[6],
                "fixed_versions": key[7],
                "reports": sorted(reports),
            }
            for key, reports in sorted(findings.items())
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize(args.directory, args.run_url), indent=2) + "\n")


if __name__ == "__main__":
    main()
