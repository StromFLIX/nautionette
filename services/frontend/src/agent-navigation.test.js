import { test } from 'node:test'
import assert from 'node:assert/strict'
import { AGENT_NAVIGATION_KEY, createAgentNavigation } from './agent-navigation.js'

const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function fixture () {
  const values = new Map(), pending = []
  let scope = 'https://one.example'
  const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
  const create = () => createAgentNavigation({ scope: () => scope, storage: () => storage, fetchAgents: () => {
    const request = deferred()
    pending.push(request)
    return request.promise
  } })
  return { values, pending, create, setScope: value => { scope = value } }
}
const agent = { id: 'ada', name: 'Ada', image: 'portrait', config: { system_prompt: 'private' } }

test('reload restores usable names immediately, without caching portraits or configuration', () => {
  const f = fixture(), nav = f.create()
  nav.sync([agent])
  assert.deepEqual(nav.state.agents, [agent])
  assert.deepEqual(JSON.parse([...f.values.values()][0]), [{ id: 'ada', name: 'Ada' }])
  const reloaded = f.create()
  assert.deepEqual(reloaded.state.agents, [{ id: 'ada', name: 'Ada' }])
  assert.equal(reloaded.state.loaded, false) // Cached IDs are not authoritative for deletion redirects.
})

test('an authoritative edit and a newer read each defeat delayed agent responses', async () => {
  const f = fixture(), nav = f.create()
  const stale = nav.load()
  nav.sync([{ ...agent, name: 'Updated' }])
  f.pending[0].resolve({ agents: [agent] })
  await stale
  assert.equal(nav.state.agents[0].name, 'Updated')
  const old = nav.load(), latest = nav.load()
  f.pending[2].resolve({ agents: [] })
  await latest
  f.pending[1].resolve({ agents: [agent] })
  await old
  assert.deepEqual(nav.state.agents, [])
  assert.deepEqual(f.create().state.agents, [])
})

test('failure retains cached navigation, exposes retry, and successful retry replaces it', async () => {
  const f = fixture()
  f.create().sync([agent])
  const nav = f.create(), failed = nav.load()
  f.pending[0].reject(new Error('Offline'))
  await failed
  assert.equal(nav.state.error, 'Offline')
  assert.equal(nav.state.loaded, false)
  assert.equal(nav.state.agents[0].name, 'Ada')
  const retry = nav.load()
  f.pending[1].resolve({ agents: [] })
  await retry
  assert.equal(nav.state.error, '')
  assert.equal(nav.state.loaded, true)
  assert.deepEqual(nav.state.agents, [])
})

test('backend switches and credential changes clear navigation and ignore in-flight responses', async () => {
  const f = fixture(), nav = f.create()
  nav.sync([agent])
  f.setScope('https://two.example')
  assert.deepEqual(f.create().state.agents, [])
  f.setScope('https://one.example')
  const old = nav.load()
  nav.clear()
  f.pending[0].resolve({ agents: [agent] })
  await old
  assert.deepEqual(nav.state.agents, [])
  assert.deepEqual(f.create().state.agents, [])
  const switched = nav.load()
  f.setScope('https://two.example')
  f.pending[1].resolve({ agents: [agent] })
  await switched
  assert.deepEqual(nav.state.agents, [])
})

test('invalid caches and unavailable storage never block navigation', async () => {
  const f = fixture()
  const key = `${AGENT_NAVIGATION_KEY}:${encodeURIComponent('https://one.example')}`
  for (const raw of ['not json', '{}', 'null', '[null,{}, {"id":"x","name":8}]']) {
    f.values.set(key, raw)
    assert.deepEqual(f.create().state.agents, [])
  }
  const nav = createAgentNavigation({ scope: () => 'one', storage: () => { throw new Error('Denied') }, fetchAgents: async () => ({ agents: [agent] }) })
  await nav.load()
  assert.equal(nav.state.loaded, true)
  assert.equal(nav.state.agents[0].id, 'ada')
  nav.clear()
  assert.deepEqual(nav.state.agents, [])
})
