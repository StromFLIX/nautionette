import { test } from 'node:test'
import assert from 'node:assert/strict'
import { desktopOAuth, oauthConnection } from './mcp-oauth.js'
const authorization = (id = 'capture-1', port = 40001) => ({ id, port, redirect_uri: `http://localhost:${port}`,
  authorization_url: 'https://login.example.test/authorize', state: 'state', response_mode: 'form_post' })

function fixture () {
  const calls = [], prepared = []
  let forwarded = 0
  const backend = {
    startMcpOAuth: async (name, config) => { calls.push(['start', name, config]); return { id: 'backend-login' } },
    pollMcpOAuth: async () => forwarded ? { status: 'complete' } : { status: 'authorizing', authorization: authorization() },
    forwardMcpOAuth: async (id, callback) => { forwarded++; calls.push(['forward', id, callback]); return { status: 'forwarded' } },
    cancelMcpOAuth: async id => { calls.push(['cancel', id]) }
  }
  const bridge = { version: 1,
    prepare: async redirect => { prepared.push(redirect); return { id: 'receiver-id', port: 40001, redirect_uri: redirect.replace(':0', ':40001') } },
    authorize: async payload => { assert.equal(payload.id, 'receiver-id'); return { method: 'POST', fields: { state: 'state', code: 'private-code' } } },
    cancel: async () => {}
  }
  return { backend, bridge, calls, prepared }
}

test('web, mobile and old markers are not desktop OAuth capabilities', () => {
  for (const marker of [undefined, true, { oauth: { version: 0 } }]) {
    globalThis.nautionetteDesktop = marker
    assert.equal(desktopOAuth(), null)
  }
  delete globalThis.nautionetteDesktop
})

test('CLI-owned login uses detected port and preserves POST; receiver ID never overwrites capture ID', async () => {
  const f = fixture()
  const controller = oauthConnection({ ...f, interval: 1, context: () => 'instance' })
  await controller.connect('example', { command: 'cli' })
  assert.deepEqual(f.prepared, ['http://localhost:40001'])
  assert.deepEqual(f.calls.find(c => c[0] === 'forward'), ['forward', 'backend-login', {
    capture_id: 'capture-1', method: 'POST', fields: { state: 'state', code: 'private-code' }
  }])
  assert.equal(f.calls[0][2].desktop_bridge_version, 1)
})

test('hosted OAuth prepares once before the backend mints PKCE and uses the exact redirect', async () => {
  const f = fixture()
  f.backend.startMcpOAuth = async (_name, config) => {
    assert.deepEqual(f.prepared, ['http://localhost:0'])
    assert.equal(config.redirect_uri, 'http://localhost:40001')
    assert.equal(config.redirect_uri_template, 'http://localhost:0')
    return { id: 'backend-login' }
  }
  await oauthConnection({ ...f, interval: 1, context: () => 'instance' }).connect('example', { transport: 'http', redirect_uri: 'http://localhost:0' })
  assert.equal(f.prepared.length, 1)
})

test('another CLI authorization opens its new port without replaying the old callback', async () => {
  const f = fixture()
  let count = 0
  f.backend.pollMcpOAuth = async () => count >= 2 ? { status: 'complete' } : {
    status: 'authorizing', authorization: authorization(`capture-${count}`, 40001 + count)
  }
  f.backend.forwardMcpOAuth = async (_id, callback) => { f.calls.push(callback); count++; return { status: 'forwarded' } }
  await oauthConnection({ ...f, interval: 1, context: () => 'instance' }).connect('example', {})
  assert.deepEqual(f.prepared, ['http://localhost:40001', 'http://localhost:40002'])
  assert.deepEqual(f.calls.filter(c => c.capture_id).map(c => c.capture_id), ['capture-0', 'capture-1'])
})

test('lost callback response is never automatically replayed', async () => {
  const f = fixture()
  let attempts = 0
  f.backend.forwardMcpOAuth = async () => { attempts++; throw new Error('Lost callback result; start again') }
  await assert.rejects(oauthConnection({ ...f, interval: 1, context: () => 'instance' }).connect('example', {}), /Lost callback/)
  assert.equal(attempts, 1)
  assert.ok(f.calls.some(c => c[0] === 'cancel'))
})

test('cancel during a slow start still cancels the late-created gateway job', async () => {
  const f = fixture()
  let resolve
  f.backend.startMcpOAuth = () => new Promise(r => { resolve = r })
  const controller = oauthConnection({ ...f, interval: 1, context: () => 'instance' })
  const running = controller.connect('example', {})
  const rejected = assert.rejects(running, /cancelled/)
  await controller.cancel()
  resolve({ id: 'late-login' })
  await rejected
  assert.ok(f.calls.some(c => c[0] === 'cancel' && c[1] === 'late-login'))
  assert.equal(f.prepared.length, 0)
})

test('switching instances after callback capture cannot send the code to another backend', async () => {
  const f = fixture()
  let context = 'old'
  f.bridge.authorize = async () => { context = 'new'; return { method: 'POST', fields: { code: 'private', state: 'state' } } }
  await assert.rejects(oauthConnection({ ...f, interval: 1, context: () => context }).connect('example', {}), /Instance or credentials changed/)
  assert.ok(!f.calls.some(c => c[0] === 'forward' || c[0] === 'cancel'))
})

test('no desktop bridge cannot start the CLI', async () => {
  const f = fixture()
  await assert.rejects(oauthConnection({ ...f, bridge: null, context: () => 'instance' }).connect('example', {}), /desktop app/)
  assert.equal(f.calls.length, 0)
})
