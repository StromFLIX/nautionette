import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createMcpAuthState, MCP_SNOOZE_KEY } from './mcp-auth-state.js'
const expired = { name: 'server', status: 'reauth_required', revision: 'same-incident' }
function memory() {
  const values = new Map()
  return { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value) }
}
function fixture(storage = memory(), clock = [1000000]) {
  const state = { connections: [], prompt: null, busyName: '' }, timers = new Map()
  let instance = 'https://instance-a.test', serial = 0
  const tracker = createMcpAuthState({ state, scope: () => instance + '|secret-api-token',
    deviceScope: () => instance, storage: () => storage, now: () => clock[0],
    fetchStatus: async () => ({ connections: [expired] }),
    schedule: fn => { timers.set(++serial, fn); return serial }, unschedule: id => timers.delete(id) })
  return { state, tracker, storage, clock, timers, instance: value => { instance = value } }
}

test('snooze persists after reload on this device without credentials; another device still prompts', async () => {
  const a = fixture(), b = fixture()
  await a.tracker.refresh(); await b.tracker.refresh()
  assert.equal(a.tracker.snooze(3600000), true)
  assert.equal(a.state.prompt, null)
  assert.equal(b.state.prompt.name, 'server')
  assert.equal(a.state.snoozedUntil, a.clock[0] + 3600000)
  assert.ok(!a.storage.getItem(MCP_SNOOZE_KEY).includes('secret-api-token'))
  const reloaded = fixture(a.storage, a.clock)
  await reloaded.tracker.refresh()
  assert.equal(reloaded.state.prompt, null)
  reloaded.tracker.dispose(); a.tracker.dispose(); b.tracker.dispose()
})

test('expiration checks status and prompts again for the same incident without opening a browser', async () => {
  const f = fixture()
  await f.tracker.refresh(); f.tracker.snooze(3600000)
  const timer = [...f.timers.values()][0]
  f.clock[0] += 3600001
  timer(); await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.state.snoozedUntil, 0)
  assert.equal(f.state.prompt.name, 'server')
  f.tracker.dispose()
})

test('snooze is per instance; manual Login bypasses it and Resume removes it', async () => {
  const f = fixture()
  await f.tracker.refresh(); f.tracker.snooze(86400000)
  f.instance('https://instance-b.test'); f.tracker.clear(); await f.tracker.refresh()
  assert.equal(f.state.prompt.name, 'server')
  f.instance('https://instance-a.test'); f.tracker.clear(); await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
  f.tracker.open(expired)
  assert.equal(f.state.prompt.name, 'server')
  f.tracker.dismiss(); await f.tracker.resume()
  assert.equal(f.state.prompt.name, 'server')
  assert.equal(f.state.snoozedUntil, 0)
  assert.deepEqual(JSON.parse(f.storage.getItem(MCP_SNOOZE_KEY)), {})
  f.tracker.dispose()
})

test('another window on this device observes snooze changes, but unrelated instances do not reset dismissal', async () => {
  const storage = memory(), handlers = new Set(), events = {
    addEventListener: (name, fn) => { if (name === 'storage') handlers.add(fn) },
    removeEventListener: (_name, fn) => handlers.delete(fn)
  }
  const state = { connections: [], prompt: null, busyName: '' }
  const tracker = createMcpAuthState({ state, scope: () => 'https://instance-a.test|private',
    deviceScope: () => 'https://instance-a.test', storage: () => storage, events,
    fetchStatus: async () => ({ connections: [expired] }), schedule: () => 1, unschedule: () => {} })
  await tracker.refresh(); tracker.dismiss()
  storage.setItem(MCP_SNOOZE_KEY, JSON.stringify({ 'https://other.test': Date.now() + 3600000 }))
  for (const fn of handlers) fn({ key: MCP_SNOOZE_KEY })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(state.prompt, null)
  storage.setItem(MCP_SNOOZE_KEY, JSON.stringify({ 'https://instance-a.test': Date.now() + 3600000 }))
  for (const fn of handlers) fn({ key: MCP_SNOOZE_KEY })
  assert.ok(state.snoozedUntil > Date.now())
  storage.setItem(MCP_SNOOZE_KEY, '{}')
  for (const fn of handlers) fn({ key: MCP_SNOOZE_KEY })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(state.prompt.name, 'server')
  tracker.dispose()
})

test('browsers denying storage still snooze for this session; invalid durations do nothing', async () => {
  const f = fixture({ getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } })
  await f.tracker.refresh()
  for (const duration of [0, -1, Infinity, 1, 604800001]) assert.equal(f.tracker.snooze(duration), false)
  assert.equal(f.tracker.snooze(3600000), true)
  await f.tracker.refresh()
  assert.equal(f.state.prompt, null)
  assert.equal(f.state.snoozedUntil, f.clock[0] + 3600000)
  assert.equal(f.timers.size, 1)
  await f.tracker.resume()
  assert.equal(f.state.prompt.name, 'server')
  f.tracker.dispose()
})
