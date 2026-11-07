import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createMcpAuthState } from './mcp-auth-state.js'

function fixture () {
  const state = { connections: [], prompt: null, busyName: '' }
  let scope = 'instance-1', connections = []
  const tracker = createMcpAuthState({ state, scope: () => scope, fetchStatus: async () => ({ connections }) })
  return { state, tracker, rows: value => { connections = value }, scope: value => { scope = value } }
}
const expired = (name = 'server', revision = '1') => ({ name, revision, mode: 'https', status: 'reauth_required' })

test('expiration shows a small prompt once, not a browser or configuration form', async () => {
  const f = fixture()
  f.rows([expired()])
  await f.tracker.refresh()
  assert.equal(f.state.prompt.name, 'server')
  f.tracker.dismiss()
  await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
  f.rows([expired('server', '2')])
  await f.tracker.refresh()
  assert.equal(f.state.prompt.revision, '2')
})

test('normal refresh and missing storage keys are not login prompts', async () => {
  const f = fixture()
  f.rows([{ ...expired(), status: 'connected' }, { ...expired('key'), status: 'storage_unavailable' }])
  await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
})

test('current sign-in suppresses duplicate automatic prompts and manual login still works', async () => {
  const f = fixture()
  f.rows([expired()])
  f.tracker.begin('server')
  await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
  f.tracker.finish('server')
  f.tracker.open(expired())
  assert.equal(f.state.prompt.name, 'server')
})

test('successful reconnection clears an idle expiry prompt', async () => {
  const f = fixture()
  f.rows([expired()]); await f.tracker.refresh()
  f.rows([{ ...expired(), status: 'connected' }]); await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
})

test('instance changes discard delayed status responses and old prompts', async () => {
  const state = { connections: [], prompt: null, busyName: '' }
  let scope = 'old', release
  const tracker = createMcpAuthState({ state, scope: () => scope, fetchStatus: () => new Promise(resolve => { release = resolve }) })
  const pending = tracker.refresh()
  scope = 'new'; tracker.clear()
  release({ connections: [expired()] }); await pending
  assert.deepEqual(state.connections, [])
  assert.equal(state.prompt, null)
})
