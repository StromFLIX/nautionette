import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { test } from 'node:test';

const source = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('agent images no longer ship the network permission extension or status handoff', () => {
  assert.equal(existsSync(new URL('../../images/pi-base/internet.ts', import.meta.url)), false);
  assert.doesNotMatch(source('images/pi-base/Dockerfile'), /internet\.ts/);
  assert.doesNotMatch(source('images/pi-base/agent-run.mjs'), /NAUTIONETTE_INTERNET_STATUS|internet_status/);
});

test('default sandbox guidance allows direct network operations without a permission tool', () => {
  const instructions = source('images/agent-sets/default/AGENTS.md');
  assert.match(instructions, /Sandboxes have internet access/);
  assert.doesNotMatch(instructions, /request_internet_access|direct-egress gate/);
});
