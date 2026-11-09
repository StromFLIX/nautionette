import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createMcpAuthState } from './mcp-auth-state.js'

const expired = (name = 'server', revision = '1') => ({ name, revision, mode: 'https', status: 'reauth_required' })
function fixture () {
  const state = { connections: [], prompt: null, busyName: '' }, rejected = []
  let scope = 'instance-1', connections = [expired()], requests = []
  const tracker = createMcpAuthState({ state, scope: () => scope,
    fetchStatus: async () => ({ connections, requests }),
    rejectRequests: async ids => { rejected.push(...ids); requests = requests.filter(wait => !ids.includes(wait.id)) } })
  return { state, tracker, rejected, rows: value => { connections = value },
    wait: (id = 'call-1', server = 'server') => { requests.push({ id, server }) },
    clearWaits: () => { requests = [] }, scope: value => { scope = value } }
}

test('expiry alone never prompts, including a new expiry revision', async () => {
  const f = fixture()
  await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
  f.rows([expired('server', '2')]); await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
})

test('a tool demand prompts; dismiss fails it; a different tool demand can prompt again', async () => {
  const f = fixture()
  f.wait(); await f.tracker.refresh()
  assert.equal(f.state.prompt.name, 'server')
  f.tracker.dismiss(); await f.tracker.refresh()
  assert.deepEqual(f.rejected, ['call-1'])
  assert.equal(f.state.prompt, null)
  f.wait('call-2'); await f.tracker.refresh()
  assert.deepEqual(f.state.prompt.requests, ['call-2'])
})

test('concurrent calls to the same server are all failed by Skip', async () => {
  const f = fixture()
  f.wait(); await f.tracker.refresh()
  f.wait('call-2'); await f.tracker.refresh()
  f.tracker.dismiss()
  assert.deepEqual(f.rejected, ['call-1', 'call-2'])
})

test('refreshing credentials and storage problems are not login prompts', async () => {
  const f = fixture()
  f.wait(); f.wait('call-2', 'key')
  f.rows([{ ...expired(), status: 'refreshing' }, { ...expired('key'), status: 'storage_unavailable' }])
  await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
})

test('manual login works without a waiting tool and while an automatic prompt is suppressed', async () => {
  const f = fixture()
  f.tracker.begin('server'); f.wait(); await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
  f.tracker.finish('server'); f.tracker.open(expired())
  assert.equal(f.state.prompt.name, 'server')
})

test('successful login and timed-out waits clear prompts without failing resumed calls', async () => {
  const f = fixture()
  f.wait(); await f.tracker.refresh()
  f.rows([{ ...expired(), status: 'connected' }]); await f.tracker.refresh()
  f.tracker.dismiss(false)
  assert.equal(f.state.prompt, null)
  assert.deepEqual(f.rejected, [])
  f.rows([expired()]); f.wait('call-2'); await f.tracker.refresh()
  f.clearWaits(); await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
})

test('instance changes discard delayed status responses and old prompts', async () => {
  const state = { connections: [], prompt: null, busyName: '' }
  let scope = 'old', release
  const tracker = createMcpAuthState({ state, scope: () => scope, fetchStatus: () => new Promise(resolve => { release = resolve }) })
  const pending = tracker.refresh()
  scope = 'new'; tracker.clear()
  release({ connections: [expired()], requests: [{ id: 'old', server: 'server' }] }); await pending
  assert.deepEqual(state.connections, [])
  assert.equal(state.prompt, null)
})
