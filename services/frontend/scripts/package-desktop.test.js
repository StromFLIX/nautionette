import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { packageWithRetry, isTransientDownloadFailure } from './package-desktop.mjs'

function fixture(results) {
  const calls = []
  const delays = []
  const messages = []
  return {
    calls, delays, messages,
    run: () => packageWithRetry(async () => {
      calls.push(true)
      return results[Math.min(calls.length - 1, results.length - 1)]
    }, {
      wait: async delay => { delays.push(delay) },
      log: message => { messages.push(message) },
    }),
  }
}

test('successful packaging runs once without waiting', async () => {
  const f = fixture([{ code: 0, output: 'built' }])
  assert.equal(await f.run(), 0)
  assert.equal(f.calls.length, 1)
  assert.deepEqual(f.delays, [])
})

test('the v1.11.0 Electron download 504 is retried and can recover', async () => {
  const f = fixture([
    { code: 1, output: '⨯ Response code 504 (Gateway Time-out) failedTask=build stackTrace=HTTPError' },
    { code: 0, output: 'built' },
  ])
  assert.equal(await f.run(), 0)
  assert.equal(f.calls.length, 2)
  assert.deepEqual(f.delays, [30_000])
  assert.match(f.messages[0], /retrying packaging/)
})

test('persistent download failure is bounded and retains failure status', async () => {
  const f = fixture([{ code: 7, output: 'Response code 503 (Service Unavailable)' }])
  assert.equal(await f.run(), 7)
  assert.equal(f.calls.length, 3)
  assert.deepEqual(f.delays, [30_000, 60_000])
})

test('rate limits and transient network errors qualify for retry', () => {
  for (const output of ['Response code 429', 'Response code 502', 'read ECONNRESET', 'ETIMEDOUT', 'EAI_AGAIN', 'ENOTFOUND', 'write EPIPE']) {
    assert.equal(isTransientDownloadFailure(output), true, output)
  }
})

test('compile, checksum, signing, authentication and missing assets fail immediately', async () => {
  for (const output of [
    'SyntaxError: Unexpected token', 'checksum mismatch', 'code signing failed',
    'Response code 401', 'Response code 403', 'Response code 404',
  ]) {
    const f = fixture([{ code: 1, output }])
    assert.equal(await f.run(), 1)
    assert.equal(f.calls.length, 1, output)
    assert.deepEqual(f.delays, [])
  }
})

test('CLI forwards platform arguments, streams logs and preserves the exit code', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'desktop-package-'))
  try {
    const npm = path.join(dir, 'fake npm.cjs')
    writeFileSync(npm, `
      console.log(JSON.stringify(process.argv.slice(2)))
      console.error('packager diagnostic')
      process.exitCode = Number(process.env.TEST_BUILD_EXIT)
    `)
    for (const code of [0, 9]) {
      const result = spawnSync(process.execPath, [fileURLToPath(new URL('./package-desktop.mjs', import.meta.url)), '--mac', '--arm64'], {
        env: { ...process.env, npm_execpath: npm, TEST_BUILD_EXIT: String(code) },
        encoding: 'utf8',
        timeout: 5000,
      })
      assert.equal(result.status, code, result.stderr)
      assert.deepEqual(JSON.parse(result.stdout), ['run', 'desktop:build', '--', '--mac', '--arm64'])
      assert.match(result.stderr, /packager diagnostic/)
      assert.doesNotMatch(result.stderr, /retrying/)
    }
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('process launch errors and cancellation propagate without retry', async () => {
  let calls = 0
  await assert.rejects(packageWithRetry(async () => {
    calls++
    throw new Error('Desktop packaging terminated by SIGTERM')
  }), /SIGTERM/)
  assert.equal(calls, 1)
})
