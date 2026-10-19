import assert from 'node:assert/strict'
import { test } from 'node:test'
import { api, ApiError } from './api.js'

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
