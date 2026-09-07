#!/bin/bash
# Run inside pi-base, including as the broker's unprivileged project user.
set -euo pipefail

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$work"

node --version
npm --version
uv --version
uvx --version
git --version
rg --version
curl --version
pkg-config --version
make --version
python -c 'import sys; assert sys.version_info[:2] == (3, 13), sys.version'
python3 -c 'import ssl, sqlite3, venv'
uv venv --offline --python 3.13 .venv
.venv/bin/python -c 'import ssl, sqlite3'

printf 'int main(void) { return 0; }\n' > hello.c
cc hello.c -o hello
./hello
c++ hello.c -o hello-cpp
./hello-cpp

# Resolve the globally installed package without depending on project node_modules.
export PLAYWRIGHT_MODULE="$(npm root -g)/playwright/index.mjs"
node --input-type=module <<'JS'
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href)
// Check both the default headless shell and the full Chromium binary.
for (const channel of [undefined, 'chromium']) {
  const browser = await chromium.launch({ channel })
  try {
    const page = await browser.newPage()
    await page.setContent('<title>Pi development tools</title><h1>Ready</h1>')
    assert.equal(await page.title(), 'Pi development tools')
    assert.equal(await page.locator('h1').textContent(), 'Ready')
    await page.screenshot()
  } finally {
    await browser.close()
  }
}
JS
