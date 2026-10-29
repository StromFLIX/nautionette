from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
from types import SimpleNamespace

import pytest
from nautionette.artcraft import (
    architecture,
    asset_name,
    bundled_release,
    catalog,
    release,
    runtime_selection,
)


@pytest.fixture
def installer(repo_root):
    spec = importlib.util.spec_from_file_location(
        "artcraft_installer", repo_root / "images/pi-base/artcraft/install.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_is_architecture_complete_and_shared_with_installer(installer):
    assert installer.CATALOG == catalog() and len(catalog()) == 13
    for app, item in catalog().items():
        assert set(item["sha256"]) == {"x86_64", "aarch64"}
        assert installer.validate(bundled_release(app)) == release(bundled_release(app))
        assert installer.asset_url(bundled_release(app)).endswith(
            asset_name(app, item["version"], architecture())
        )
    assert catalog()["vectorcraft"]["mcp_args"] == ["--headless"]
    assert "/pdfcraft-cli-" in installer.asset_url(bundled_release("pdfcraft"))


@pytest.mark.parametrize(
    "change",
    [
        {"app": "../evil"},
        {"app": ["cadcraft"]},
        {"version": "latest"},
        {"version": "1.0.0/../../x"},
        {"version": None},
        {"arch": "windows"},
        {"sha256": "f" * 63},
        {"sha256": "G" * 64},
        {"url": "https://evil"},
    ],
)
def test_release_contracts_reject_unfixed_sources(installer, change):
    bad = {**bundled_release("cadcraft"), **change}
    for validate in (installer.validate, release):
        with pytest.raises(ValueError):
            validate(bad)


def test_runtime_pins_require_owned_artifacts_and_unique_apps():
    item = {"app": "cadcraft", "release": bundled_release("cadcraft")}
    assert runtime_selection([item]) == [item]
    updated = {**item, "release": {**item["release"], "version": "1.2.3"}}
    for bad in (
        None,
        {},
        [item, item],
        [updated],
        [{**updated, "installation_id": "../evil"}],
        [{**item, "args": ["--bad"]}],
        [{**item, "app": "wordcraft"}],
    ):
        with pytest.raises(ValueError):
            runtime_selection(bad)
    assert runtime_selection([{**updated, "installation_id": "a" * 32}])


def archive(tmp_path, entries):
    path = tmp_path / "release.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, value in entries:
            member = tarfile.TarInfo(name)
            if isinstance(value, str):
                member.type, member.linkname = tarfile.SYMTYPE, value
                tar.addfile(member)
            else:
                member.size = len(value)
                tar.addfile(member, io.BytesIO(value))
    return path


def test_extraction_keeps_cli_and_upstream_licenses_not_desktop_or_wasm(installer, tmp_path):
    path = archive(
        tmp_path,
        [
            ("release/share/bash-completion/completions/cadcraft-cli", b"completion script"),
            ("release/bin/cadcraft-cli", b"binary"),
            ("release/README.md", b"readme"),
            ("release/doc/LICENSE.font", b"font attribution"),
            ("release/LICENSE", b"MIT"),
            ("release/cadcraft-desktop", b"desktop"),
            ("release/app.wasm", b"wasm"),
        ],
    )
    target = tmp_path / "target"
    installer.extract(path, target, "cadcraft")
    assert (target / "bin/cadcraft-cli").read_bytes() == b"binary"
    assert (target / "bin/cadcraft-cli").stat().st_mode & 0o777 == 0o755
    assert len(list((target / "licenses").rglob("*.*"))) >= 2
    assert not list(target.rglob("*.wasm")) and not list(target.rglob("*desktop*"))


@pytest.mark.parametrize(
    "entries",
    [
        [("../cadcraft-cli", b"evil")],
        [("/etc/cadcraft-cli", b"evil")],
        [("release\\cadcraft-cli", b"evil")],
        [("release/cadcraft-cli", "/etc/passwd")],
        [("one/cadcraft-cli", b"one"), ("two/cadcraft-cli", b"two")],
        [("release/cadcraft-cli", b"one"), ("release/bin/cadcraft-cli", b"two")],
        [("release/share/bash-completion/completions/cadcraft-cli", b"not a binary")],
        [("release/README.md", b"no CLI")],
    ],
)
def test_unsafe_or_ambiguous_archives_fail_closed(installer, tmp_path, entries):
    with pytest.raises(ValueError):
        installer.extract(archive(tmp_path, entries), tmp_path / "target", "cadcraft")


def test_extraction_is_size_bounded(installer, tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "MAX_EXTRACTED", 4)
    with pytest.raises(ValueError, match="exceed"):
        installer.extract(archive(tmp_path, [("r/cadcraft-cli", b"12345")]), tmp_path / "target", "cadcraft")


def test_download_verifies_exact_bytes_before_any_extraction(installer, tmp_path, monkeypatch):
    data = b"native-archive"
    calls = []

    def open_url(request, **kwargs):
        calls.append(request.full_url)
        return io.BytesIO(data)

    monkeypatch.setattr(installer.urllib.request, "urlopen", open_url)
    spec = {**bundled_release("cadcraft"), "sha256": hashlib.sha256(data).hexdigest()}
    installer.download(spec, tmp_path / "download")
    assert (tmp_path / "download").read_bytes() == data
    assert calls[0].startswith("https://github.com/storytold/cadcraft/releases/download/v")
    with pytest.raises(ValueError, match="checksum"):
        installer.download(bundled_release("cadcraft"), tmp_path / "bad")
    monkeypatch.setattr(installer, "MAX_ARCHIVE", 4)
    with pytest.raises(ValueError, match="exceeds"):
        installer.download(spec, tmp_path / "large")


def test_probe_uses_allowlisted_headless_argv_bounded_time_and_no_inherited_secrets(
    installer, tmp_path, monkeypatch
):
    captured = []

    def run(args, **kwargs):
        captured.append((args, kwargs))
        replies = [
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [{"name": "render", "inputSchema": {"type": "object"}}]},
            },
        ]
        kwargs["stdout"].write("\n".join(json.dumps(item) for item in replies).encode())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", run)
    monkeypatch.setenv("INTERNAL_TOKEN", "private")
    installer.probe(tmp_path, "vectorcraft")
    args, kwargs = captured[0]
    assert args == [str(tmp_path / "bin/vectorcraft-cli"), "mcp", "--headless"]
    assert kwargs["timeout"] == 45 and "INTERNAL_TOKEN" not in kwargs["env"]
    assert not kwargs.get("shell")
    assert json.loads(kwargs["input"].splitlines()[0])["method"] == "initialize"


def test_manifest_published_only_after_successful_probe_and_no_overwrite(installer, tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "download", lambda spec, path: None)
    monkeypatch.setattr(installer, "extract", lambda *args: None)

    def fail(*args):
        raise ValueError("incompatible native tools")

    monkeypatch.setattr(installer, "probe", fail)
    target = tmp_path / "artifact"
    with pytest.raises(ValueError):
        installer.install(bundled_release("cadcraft"), target)
    assert not (target / "manifest.json").exists()
    monkeypatch.setattr(installer, "probe", lambda *args: None)
    installer.install(bundled_release("cadcraft"), target)
    assert json.loads((target / "manifest.json").read_text()) == bundled_release("cadcraft")
    with pytest.raises(ValueError, match="empty"):
        installer.install(bundled_release("cadcraft"), target)
