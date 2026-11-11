"""Browser CI must not depend on live Ubuntu mirrors or mismatched browsers."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_uses_the_locked_playwright_browser_image_without_live_apt_setup():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    frontend = ci["jobs"]["frontend"]
    lock = json.loads((ROOT / "services/frontend/package-lock.json").read_text())
    version = lock["packages"]["node_modules/playwright"]["version"]
    assert frontend["runs-on"] == "ubuntu-24.04"
    assert frontend["container"]["image"] == f"mcr.microsoft.com/playwright:v{version}-noble"
    assert frontend["container"]["options"] == "--ipc=host"
    commands = "\n".join(step.get("run", "") for step in frontend["steps"])
    assert "playwright install" not in commands
    assert "apt-get" not in commands
    assert "npm ci" in commands
    assert "npx playwright test" in commands
    assert "--browser=webkit" in commands
    assert "tests/test_playwright_ci.py" in next(
        step["run"]
        for step in ci["jobs"]["python"]["steps"]
        if step.get("name", "").startswith("Deployment, snapshot")
    )


def test_desktop_smokes_the_packaged_electron_without_installing_unrelated_browsers():
    desktop = yaml.safe_load((ROOT / ".github/workflows/desktop.yml").read_text())["jobs"]["desktop"]
    linux = next(row for row in desktop["strategy"]["matrix"]["include"] if row["platform"] == "linux")
    assert linux["os"] == "ubuntu-24.04"
    commands = "\n".join(step.get("run", "") for step in desktop["steps"])
    assert "playwright install" not in commands
    assert "apt-get" not in commands
    smoke = next(step for step in desktop["steps"] if step.get("name") == "Test packaged Linux renderer")
    assert smoke["run"] == "xvfb-run -a node scripts/smoke-desktop.mjs"
    assert "continue-on-error" not in smoke
    script = (ROOT / "services/frontend/scripts/smoke-desktop.mjs").read_text()
    assert "out/desktop/linux-unpacked/nautionette-frontend" in script
    assert "await electron.launch(" in script
