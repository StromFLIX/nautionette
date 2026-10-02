// Run during image build: verify the pinned npm host and its public SDK imports.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = process.argv[2] || '/usr/local/lib/node_modules/@earendil-works/pi-coding-agent';
const manifest = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
assert.equal(manifest.name, '@earendil-works/pi-coding-agent');
assert.equal(manifest.version, '1.0.0', 'Unexpected Pi runtime version');
// Pi 1.0 removed pi-agent-core/node; verify the supported root export instead.
// This checks the host SDK, not compatibility with every third-party package.
const peers = ['@earendil-works/chord', '@earendil-works/chord/context', '@earendil-works/pi-agent-core', '@earendil-works/pi-ai/compat'];
// Use ESM resolution from the host root (chord has import-only exports).
const check = spawnSync(process.execPath, ['--input-type=module', '--eval',
  `for (const name of ${JSON.stringify(peers)}) await import(name);`], { cwd: root, stdio: 'inherit' });
assert.equal(check.status, 0, 'Pi host dependencies are incomplete');
const pi = await import(pathToFileURL(resolve(root, manifest.main)).href);
for (const name of ['createAgentSession', 'ModelRuntime', 'SessionManager', 'DefaultResourceLoader']) {
  assert.equal(typeof pi[name], 'function', `Missing Pi SDK export: ${name}`);
}
console.log(`Pi ${manifest.version}: public SDK runtime imports verified`);
