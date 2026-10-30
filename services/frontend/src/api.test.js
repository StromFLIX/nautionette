import assert from 'node:assert/strict'
import { test } from 'node:test'
import { api, ApiError, auth, chatStream, liveEvents, server } from './api.js'

globalThis.localStorage = { getItem: () => null }

test('structured storage errors retain their code, exact usage and actual settings link', async t => {
  const detail = { code: 'output_storage_limit_exceeded', message: 'The instance output storage limit would be exceeded.', used_bytes: 1024, limit_bytes: 2048, requested_bytes: 4096, settings_url: 'https://app.example/settings/storage' }
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail }, { status: 413 }))
  await assert.rejects(api.outputStorage(), error => {
    assert.ok(error instanceof ApiError)
    assert.equal(error.status, 413)
    assert.equal(error.message, detail.message)
    assert.deepEqual(error.detail, detail)
    return true
  })
})

test('ordinary string errors stay readable and retain their HTTP status', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'Storage service unavailable' }, { status: 503 }))
  await assert.rejects(api.outputStorage(), error => error.message === 'Storage service unavailable' && error.status === 503)
})

test('storage deletion always sends the reviewed plan ID and explicit confirmation', async t => {
  const requests = []
  t.mock.method(globalThis, 'fetch', async (path, options) => {
    requests.push({ path, ...options })
    return Response.json({ deleted_revisions: 2 })
  })
  assert.deepEqual(await api.deleteStoredOutputs('a'.repeat(32)), { deleted_revisions: 2 })
  assert.equal(requests[0].path, '/api/settings/storage/cleanup')
  assert.equal(requests[0].method, 'POST')
  assert.deepEqual(JSON.parse(requests[0].body), { plan_id: 'a'.repeat(32), confirm: true })
})

test('supervised streams encode tokens, ignore malformed frames and fence replaced instance probes', async t => {
  const storage = new Map(), sources = [], probes = [], snapshots = [], events = [], errors = []
  const previous = { localStorage: globalThis.localStorage, EventSource: globalThis.EventSource }
  globalThis.localStorage = { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }
  globalThis.EventSource = class extends EventTarget {
    readyState = 0
    constructor (url) { super(); this.url = url; sources.push(this) }
    close () { this.readyState = 2 }
  }
  t.after(() => Object.assign(globalThis, previous))
  t.mock.timers.enable({ apis: ['setTimeout'] })
  t.mock.method(globalThis, 'fetch', (url, options) => {
    probes.push({ url, ...options })
    return new Promise(() => {})
  })
  server.url = 'https://old.example/'
  auth.token = 'old+token&secret'
  const chat = chatStream('a/b', data => snapshots.push(data), error => errors.push(error))
  const global = liveEvents(event => events.push(event), error => errors.push(error))
  t.after(() => { chat.close(); global.close() })
  assert.equal(sources[0].url, 'https://old.example/api/chats/a%2Fb/stream?token=old%2Btoken%26secret')
  sources[0].onmessage({ data: '{' })
  sources[0].onmessage({ data: '{"type":"snapshot","chat":{"id":"a/b"}}' })
  sources[1].onmessage({ data: '{"kind":"connected"}' })
  assert.equal(snapshots.length, 1)
  assert.deepEqual(events, [{ kind: 'connected' }])
  sources[0].onerror()
  server.url = 'https://new.example'
  auth.token = 'new-token'
  t.mock.timers.tick(1000)
  assert.equal(probes.length, 1)
  assert.equal(probes[0].url, 'https://old.example/api/chats/a%2Fb/stream')
  assert.equal(probes[0].method, 'HEAD')
  assert.deepEqual(probes[0].headers, { Authorization: 'Bearer old+token&secret' })
  assert.equal(probes[0].cache, 'no-store')
  chat.close()
  assert.equal(probes[0].signal.aborted, true)
})
