"""Keep the GitHub front page small, truthful and linked to working setup docs."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PAGES = [README, ROOT / "docs/README.md", ROOT / "docs/self-hosting.md", ROOT / "docs/reference.md"]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []
        self.images = []
        self.anchors = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.targets.append(attrs["href"])
        if tag == "img" and attrs.get("src"):
            self.targets.append(attrs["src"])
            self.images.append(attrs)
        for name in ("id", "name"):
            if attrs.get(name):
                self.anchors.add(attrs[name])


def without_fences(text):
    return re.sub(r"(?ms)^```[^\n]*\n.*?^```[^\n]*(?:\n|$)", "", text)


def anchors(text):
    text = without_fences(text)
    html = Links()
    html.feed(text)
    result = html.anchors.copy()
    seen = {}
    for heading in re.findall(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$", text):
        heading = re.sub(r"<[^>]+>", "", heading).lower()
        slug = re.sub(r"[^\w\s-]", "", heading).replace(" ", "-")
        count = seen.get(slug, 0)
        result.add(f"{slug}-{count}" if count else slug)
        seen[slug] = count + 1
    return result


def test_readme_is_a_short_branded_front_page_with_real_images_and_status_badges():
    text = README.read_text()
    brand = json.loads((ROOT / "branding/brand.json").read_text())
    assert f">{brand['name']}</h1>" in text
    assert brand["tagline"] in text
    assert len(text.splitlines()) < 230
    assert "sslip.io" not in text
    assert "actions/workflows/ci.yml/badge.svg?branch=main" in text
    repository = brand["repository"].removeprefix("https://github.com/")
    assert f"img.shields.io/github/v/release/{repository}" in text
    html = Links()
    html.feed(text)
    screenshots = [image for image in html.images if image["src"].startswith("services/website/assets/")]
    assert [image["src"] for image in screenshots] == [
        "services/website/assets/product-tour/chat.png",
        "services/website/assets/product/phone.png",
    ]
    assert all(image.get("alt", "").startswith(f"Actual {brand['name']}") for image in screenshots)
    assert "workspace-demo" not in text
    assert "data:image" not in text
    if not (ROOT / "LICENSE").exists():
        assert "does not yet include a project-wide `LICENSE` file" in text
        assert not re.search(r"shields\.io/[^\s\"]*license", text, re.I)
    else:
        assert "does not yet include a project-wide `LICENSE` file" not in text


def test_readme_and_setup_use_the_real_compose_entrypoint_and_required_secrets():
    readme = README.read_text()
    setup = (ROOT / "docs/self-hosting.md").read_text()
    env = (ROOT / ".env.example").read_text()
    compose = (ROOT / "docker-compose.yaml").read_text()
    assert "BACKEND_PORT=8080" in env
    assert "WEBSITE_PORT=8081" in env
    assert '"127.0.0.1:${BACKEND_PORT:-8080}:8080"' in compose
    for text in [readme, setup]:
        for key in ["APP_TOKEN", "INTERNAL_TOKEN", "POSTGRES_PASSWORD", "MAXIO_SECRET_KEY"]:
            assert f"`{key}`" in text
            assert f"{key}=" in env
        for command in [
            "cp .env.example .env",
            "docker compose config --quiet",
            "docker compose up -d --build",
        ]:
            assert command in text
        assert "http://localhost:8080" in text
        assert "28+" in text
        assert "openssl rand -hex 32" in text
    assert "BACKEND_PORT:-8080" in compose
    assert "Docker Engine 28+" in (ROOT / "docs/mini-apps.md").read_text()


def test_documentation_paths_and_local_section_links_resolve():
    for page in PAGES:
        text = without_fences(page.read_text())
        html = Links()
        html.feed(text)
        targets = html.targets + re.findall(r"\[[^\]\n]+\]\(([^)\n]+)\)", text)
        for target in targets:
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            path = (page.parent / unquote(url.path)).resolve() if url.path else page
            assert path.is_relative_to(ROOT), f"{page.name}: outside repository: {target}"
            assert path.exists(), f"{page.relative_to(ROOT)}: missing {target}"
            if url.fragment and path.suffix == ".md":
                assert unquote(url.fragment) in anchors(path.read_text()), (
                    f"{page.relative_to(ROOT)}: missing section {target}"
                )


def test_existing_website_readme_deep_links_keep_working():
    html = (ROOT / "services/website/index.html").read_text()
    repository = json.loads((ROOT / "branding/brand.json").read_text())["repository"]
    fragments = re.findall(re.escape(repository) + r"#([a-z0-9-]+)", html)
    assert {"run-it", "mini-apps", "workflows-are-python-files"}.issubset(fragments)
    assert set(fragments).issubset(anchors(README.read_text()))


def test_useful_runtime_details_remain_accessible_without_stale_open_points():
    reference = (ROOT / "docs/reference.md").read_text()
    for heading in [
        "## Architecture",
        "### Agent-to-agent delegation",
        "### Safe automatic continuation",
        "## GitHub Projects",
        "### Generated outputs",
        "## The authoring schema",
    ]:
        assert heading in reference
    assert "## Open points" not in reference
    assert "test commit 2026" not in reference
    assert "Nothing survives in a Pi container" not in reference
    assert "no store behind it yet" not in reference
