// Run during image build: verify the npm host and imports used by SDK children.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = process.argv[2] || '/usr/local/lib/node_modules/@earendil-works/pi-coding-agent';
const manifest = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
assert.equal(manifest.name, '@earendil-works/pi-coding-agent');
const peers = ['@earendil-works/chord', '@earendil-works/chord/context', '@earendil-works/pi-agent-core/node', '@earendil-works/pi-ai/compat'];
// Use ESM resolution from the host root (chord has import-only exports).
const check = spawnSync(process.execPath, ['--input-type=module', '--eval',
  `for (const name of ${JSON.stringify(peers)}) await import(name);`], { cwd: root, stdio: 'inherit' });
assert.equal(check.status, 0, 'Pi host dependencies are incomplete');
const pi = await import(pathToFileURL(resolve(root, manifest.main)).href);
for (const name of ['createAgentSession', 'ModelRuntime', 'SessionManager', 'DefaultResourceLoader']) {
  assert.equal(typeof pi[name], 'function', `Missing Pi SDK export: ${name}`);
}
console.log(`Pi ${manifest.version}: child-session runtime imports verified`);
