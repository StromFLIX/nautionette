import json
import math

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from scripts.sync_brand import ROOT, expected_files, sync, validate_brand


def test_generated_branding_matches_sources():
    assert sync(check=True) == []
    assert expected_files() == expected_files()


def test_brand_config_rejects_unsafe_copy_and_non_color_css():
    brand = json.loads((ROOT / "branding/brand.json").read_text())
    for name in ["bad\nname", "../name", "<script>", ""]:
        with pytest.raises(ValueError):
            validate_brand({**brand, "name": name})
    with pytest.raises(ValueError, match="six-digit hex"):
        validate_brand({**brand, "colors": {**brand["colors"], "paper": "url(https://example.test)"}})


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


def test_original_solid_logo_has_no_nested_strokes_or_raster_dependency():
    logo = (ROOT / "branding/logo.svg").read_text()
    assert logo.count("<path ") == 1
    assert "stroke=" not in logo
    assert "opacity=" not in logo
    assert "<image" not in logo
    assert "base64" not in logo


def test_android_foreground_is_a_transparent_glyph_not_a_second_rounded_tile():
    with Image.open(ROOT / "branding/native/ic_launcher_foreground.png") as source:
        foreground = source.convert("RGBA")
    assert foreground.size == (432, 432)  # Android's 108dp adaptive layer at xxxhdpi.
    alpha = foreground.getchannel("A")
    assert alpha.getextrema() == (0, 255)
    assert alpha.getpixel((0, 0)) == alpha.getpixel((431, 431)) == 0
    assert foreground.getpixel((220, 200)) == (255, 255, 255, 255)
    assert foreground.getpixel((180, 431)) == (255, 255, 255, 255)  # Open tail bleeds to the mask.
    # No opaque canvas/outer squircle can silently return in a future export.
    assert alpha.getbbox() == (156, 148, 283, 432)
    for pixel in foreground.get_flattened_data():
        if pixel[3]:
            assert pixel[:3] == (255, 255, 255)


def android_launcher_images():
    with Image.open(ROOT / "branding/native/ic_launcher.png") as source:
        legacy = source.convert("RGB")
    with Image.open(ROOT / "branding/native/ic_launcher_foreground.png") as source:
        foreground = source.convert("RGBA")
    background = Image.new("RGBA", foreground.size, "black")
    adaptive = Image.alpha_composite(background, foreground)
    # A launcher displays the middle 72dp of the 108dp adaptive layer.
    adaptive = adaptive.crop((72, 72, 360, 360)).resize((192, 192), Image.Resampling.LANCZOS).convert("RGB")
    return legacy, adaptive


def test_legacy_and_adaptive_android_icons_use_the_same_visible_scale():
    legacy, adaptive = android_launcher_images()
    assert legacy.size == adaptive.size == (192, 192)
    assert max(ImageStat.Stat(ImageChops.difference(legacy, adaptive)).mean) < 1
    # The iOS/desktop square is deliberately still separate, with white padding.
    with Image.open(ROOT / "branding/native/app-icon.png") as universal:
        assert universal.size == (1024, 1024)
        assert universal.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


@pytest.mark.parametrize("shape", ["circle", "squircle", "rounded-square"])
def test_android_launcher_masks_have_no_white_matte_or_double_rounded_corners(shape):
    mask = Image.new("L", (192, 192))
    draw = ImageDraw.Draw(mask)
    if shape == "circle":
        draw.ellipse((0, 0, 191, 191), fill=255)
    elif shape == "rounded-square":
        draw.rounded_rectangle((0, 0, 191, 191), radius=42, fill=255)
    else:
        points = []
        for degree in range(360):
            angle = math.radians(degree)
            x, y = math.cos(angle), math.sin(angle)
            points.append(
                (95.5 + 95.5 * math.copysign(abs(x) ** 0.5, x), 95.5 + 95.5 * math.copysign(abs(y) ** 0.5, y))
            )
        draw.polygon(points, fill=255)
    # Inspect the inner mask edge, excluding only the intentional open tail.
    boundary = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(7)))
    ImageDraw.Draw(boundary).rectangle((0, 153, 191, 191), fill=0)
    for icon in android_launcher_images():
        assert ImageChops.multiply(icon.convert("L"), boundary).getbbox() is None


def test_check_mode_does_not_write_stale_copies(tmp_path, monkeypatch):
    target = tmp_path / "generated.txt"
    target.write_bytes(b"old")
    monkeypatch.setattr("scripts.sync_brand.expected_files", lambda root: {target: b"new"})
    assert sync(check=True, root=tmp_path) == ["generated.txt"]
    assert target.read_bytes() == b"old"
    assert sync(root=tmp_path) == ["generated.txt"]
    assert target.read_bytes() == b"new"
