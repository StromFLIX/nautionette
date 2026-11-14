"""Installed-wheel proof that new imports do not duplicate service state."""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "denkzeug": "nautionette",
    "denkzeug_backend": "nautionette_backend",
    "denkzeug_docker_broker": "nautionette_docker_broker",
    "denkzeug_worker": "nautionette_worker",
    "denkzeug_workflow_mcp": "nautionette_workflow_mcp",
}


def test_wheels_include_aliases_and_preserve_singletons_in_both_import_orders(tmp_path):
    wheels = tmp_path / "wheels"
    uv = shutil.which("uv")
    assert uv
    subprocess.run(  # noqa: S603 - fixed workspace build, no caller-controlled command
        [uv, "build", "--all-packages", "--wheel", "--out-dir", str(wheels)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    installed = tmp_path / "installed"
    installed.mkdir()
    archives = list(wheels.glob("*.whl"))
    assert len(archives) == len(PACKAGES)
    for wheel in archives:
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(installed)  # Trusted wheels built above from this checkout.
    for alias, canonical in PACKAGES.items():
        assert (installed / alias / "__init__.py").is_file()
        assert (installed / canonical / "__init__.py").is_file()
    for alias_first in (True, False, None):
        mode = "concurrent" if alias_first is None else "alias-first" if alias_first else "canonical-first"
        data = tmp_path / mode
        data.mkdir()
        code = f"""
import importlib, pathlib, sys
sys.path.insert(0, {str(installed)!r})
pairs = {PACKAGES!r}
children = {{
    'denkzeug': ['telemetry', 'chat_sessions'],
    'denkzeug_backend': ['config', 'db', 'events',
                         'clients.temporal_server', 'integrations.registry', 'main'],
    'denkzeug_docker_broker': ['daemon'],
    'denkzeug_worker': ['loader'],
    'denkzeug_workflow_mcp': ['main'],
}}
if {alias_first is None!r}:
    from concurrent.futures import ThreadPoolExecutor
    # Initialize package trees on the startup thread, as services do before
    # starting workers. Race leaf imports, not mutually dependent package
    # initializers (alias/canonical locks cannot share a cross-name order).
    for alias, canonical in pairs.items():
        for suffix in children[alias]:
            parent = canonical + ('.' + suffix.rsplit('.', 1)[0] if '.' in suffix else '')
            importlib.import_module(parent)
    names = [root + '.' + suffix for alias, canonical in pairs.items()
             for suffix in children[alias] for root in (alias, canonical)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(importlib.import_module, names))
for alias, canonical in pairs.items():
    for suffix in ['', *children[alias]]:
        new = alias + ('.' + suffix if suffix else '')
        old = canonical + ('.' + suffix if suffix else '')
        names = (new, old) if {alias_first!r} else (old, new)
        first = importlib.import_module(names[0])
        second = importlib.import_module(names[1])
        assert first is second, (new, old)
        assert first.__name__ == first.__spec__.name == old, (new, first.__spec__)
        assert pathlib.Path(first.__file__).is_relative_to({str(installed)!r})
        first.alias_test_sentinel = object()
        assert second.alias_test_sentinel is first.alias_test_sentinel
        del first.alias_test_sentinel
from denkzeug_backend.config import settings
from nautionette_backend.config import settings as old_settings
assert settings is old_settings
from denkzeug_backend.db import db
from nautionette_backend.db import db as old_db
assert db is old_db
from denkzeug_backend.main import app
from nautionette_backend.main import app as old_app
assert app is old_app
"""
        result = subprocess.run(  # noqa: S603 - fixed test code, isolated data, no network operations
            [sys.executable, "-I", "-c", code],
            cwd=tmp_path,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path),
                "DATA_DIR": str(data),
                "APP_TOKEN": "test-alias",
                "INTERNAL_TOKEN": "test-alias",
                "WORKFLOWS_DIR": str(tmp_path),
                "ARTIFACTS_DIR": str(tmp_path),
            },
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
