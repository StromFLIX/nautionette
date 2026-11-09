import json

from scripts.sync_brand import ROOT, expected_files, sync


def test_generated_branding_matches_sources():
    assert sync(check=True) == []
    assert expected_files() == expected_files()


def test_display_branding_does_not_change_technical_identity():
    package = json.loads((ROOT / "services/frontend/package.json").read_text())
    capacitor = json.loads((ROOT / "services/frontend/capacitor.config.json").read_text())
    brand = json.loads((ROOT / "branding/brand.json").read_text())
    assert package["build"]["productName"] == capacitor["appName"] == brand["name"]
    assert package["name"] == "nautionette-frontend"
    assert package["build"]["appId"] == capacitor["appId"] == "dev.nautionette.app"
    assert package["build"]["protocols"][0]["schemes"] == ["nautionette"]
    assert package["build"]["artifactName"].startswith("nautionette-")
    assert brand["repository"] == "https://github.com/StromFLIX/nautionette"


def test_approved_logo_has_curved_nested_contours_and_no_raster_dependency():
    logo = (ROOT / "branding/logo.svg").read_text()
    assert "curved_base" in logo
    assert 'opacity="0.65"' in logo
    assert "<image" not in logo
    assert "base64" not in logo


def test_check_mode_does_not_write_stale_copies(tmp_path, monkeypatch):
    target = tmp_path / "generated.txt"
    target.write_bytes(b"old")
    monkeypatch.setattr("scripts.sync_brand.expected_files", lambda root: {target: b"new"})
    assert sync(check=True, root=tmp_path) == ["generated.txt"]
    assert target.read_bytes() == b"old"
    assert sync(root=tmp_path) == ["generated.txt"]
    assert target.read_bytes() == b"new"
