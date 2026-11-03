"""Fixed offline installer copied into a fresh artifact volume, never supplied by a caller."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

MAX_ARCHIVE = 256 * 1024 * 1024


def install_offline(spec, target: Path, installer):
    spec = installer.validate(spec)
    source = target / ".release.tar.gz"
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_ARCHIVE:
        raise ValueError("Invalid staged ArtCraft archive")
    digest = hashlib.sha256()
    with source.open("rb") as incoming:
        while chunk := incoming.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != spec["sha256"]:
        raise ValueError("Staged ArtCraft release checksum mismatch")
    with tempfile.TemporaryDirectory() as work:
        archive = Path(work) / "release.tar.gz"
        shutil.move(source, archive)
        (target / ".install.py").unlink()
        if any(target.iterdir()):
            raise ValueError("Installation target must be empty")
        installer.extract(archive, target, spec["app"])
    installer.probe(target, spec["app"])
    (target / "manifest.json").write_text(json.dumps(spec))
    return spec


def main():
    module = importlib.util.spec_from_file_location(
        "artcraft_installer", "/usr/local/lib/nautionette/artcraft/install.py"
    )
    installer = importlib.util.module_from_spec(module)
    module.loader.exec_module(installer)
    print(
        json.dumps(install_offline(json.loads(os.environ["ARTCRAFT_RELEASE"]), Path("/artifact"), installer)),
        flush=True,
    )


if __name__ == "__main__":
    main()
