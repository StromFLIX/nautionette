import assert from 'node:assert/strict'
import { test } from 'node:test'
import { liveConnection, LIVE_STALE_MS } from './live-connection.js'

const settle = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }
function harness (probe = async () => ({ ok: true, status: 204 })) {
  let time = 0, id = 0
  const timers = new Map(), sources = [], errors = [], messages = [], probes = []
  const window = new EventTarget(), document = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  const navigator = { onLine: true }
  class Source extends EventTarget {
    readyState = 0
    constructor (url) { super(); this.url = url; sources.push(this) }
    close () { this.readyState = 2 }
    open () { this.readyState = 1; this.onopen() }
    message (data) { this.onmessage({ data }) }
    error () { this.readyState = 2; this.onerror() }
    heartbeat () { this.dispatchEvent(new Event('heartbeat')) }
  }
  const connection = liveConnection('/events?token=secret', {
    probe: signal => { probes.push(signal); return probe(signal) },
    onMessage: event => messages.push(event.data), onError: error => errors.push(error)
  }, { EventSource: Source, window, document, navigator, now: () => time, random: () => 1,
    schedule: (callback, delay) => { timers.set(++id, { callback, at: time + delay }); return id },
    cancel: id => timers.delete(id) })
  return { connection, sources, errors, messages, probes, timers, window, document, navigator,
    async tick (ms) {
      const target = time + ms
      while (true) {
        const next = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0]
        if (!next) break
        const [id, timer] = next
        timers.delete(id); time = timer.at; timer.callback(); await settle()
      }
      time = target
      await settle()
    },
    elapse: ms => { time += ms },
    fire (name) { (name === 'visibilitychange' ? document : window).dispatchEvent(new Event(name)) }
  }
}

test('terminal browser failures recover through a probe and a newly created source', async t => {
  const h = harness()
  t.after(() => h.connection.close())
  const first = h.sources[0]
  first.error()
  assert.equal(first.readyState, 2)
  await h.tick(999)
  assert.equal(h.probes.length, 0)
  await h.tick(1)
  assert.equal(h.sources.length, 2)
  h.sources[1].open()
  h.sources[1].message('fresh snapshot')
  assert.deepEqual(h.messages, ['fresh snapshot'])
  first.message('obsolete snapshot')
  first.error()
  assert.deepEqual(h.messages, ['fresh snapshot'])
  assert.equal(h.errors.length, 1)
})

test('repeated HTTP 503s back off exponentially to 30 seconds; opening alone never resets it', async t => {
  let status = 503
  const h = harness(async () => ({ ok: status === 204, status }))
  t.after(() => h.connection.close())
  h.sources[0].error()
  for (const delay of [1000, 2000, 4000, 8000, 16000, 30000, 30000]) {
    const count = h.probes.length
    await h.tick(delay - 1)
    assert.equal(h.probes.length, count)
    await h.tick(1)
    assert.equal(h.probes.length, count + 1)
    assert.equal(h.sources.length, 1)
  }
  status = 204
  await h.tick(30000)
  const next = h.sources.at(-1)
  next.open(); next.message('snapshot'); next.error()
  await h.tick(29999)
  assert.equal(h.sources.length, 2)
  await h.tick(1)
  assert.equal(h.sources.length, 3)
})

test('heartbeat/data keep idle streams alive and stable streams reset the retry delay', async t => {
  const h = harness()
  t.after(() => h.connection.close())
  h.sources[0].error()
  await h.tick(1000)
  const source = h.sources[1]
  source.open()
  await h.tick(20_000); source.heartbeat()
  await h.tick(20_000); source.heartbeat()
  await h.tick(20_000); source.message('update')
  assert.equal(h.sources.length, 2)
  source.error()
  await h.tick(1000)
  assert.equal(h.sources.length, 3)
})

for (const opened of [false, true]) test(`watchdog replaces a ${opened ? 'silently stalled OPEN' : 'hung CONNECTING'} stream`, async t => {
  const h = harness()
  t.after(() => h.connection.close())
  if (opened) h.sources[0].open()
  await h.tick(LIVE_STALE_MS - 1)
  assert.equal(h.errors.length, 0)
  await h.tick(1)
  assert.equal(h.sources[0].readyState, 2)
  assert.match(h.errors[0].message, /stopped responding/)
  await h.tick(1000)
  assert.equal(h.sources.length, 2)
})

for (const status of [401, 403, 404, 410, 422]) test(`HTTP ${status} stops retries and lifecycle wakeups`, async () => {
  const h = harness(async () => ({ ok: false, status }))
  h.sources[0].error()
  await h.tick(1000)
  assert.equal(h.errors.at(-1).status, status)
  assert.equal(h.errors.at(-1).permanent, true)
  h.fire('focus'); h.fire('online'); h.fire('pageshow'); h.fire('visibilitychange')
  await h.tick(300_000)
  assert.equal(h.probes.length, 1)
  assert.equal(h.sources.length, 1)
  assert.equal(h.timers.size, 0)
})

for (const status of [408, 409, 425, 429, 502, 504]) test(`HTTP ${status} is retryable`, async t => {
  const h = harness(async () => ({ ok: false, status }))
  t.after(() => h.connection.close())
  h.sources[0].error()
  await h.tick(1000)
  assert.equal(h.errors.at(-1).permanent, false)
  await h.tick(2000)
  assert.equal(h.probes.length, 2)
})

test('old backend HEAD 405 remains compatible during rollout', async t => {
  const h = harness(async () => ({ ok: false, status: 405 }))
  t.after(() => h.connection.close())
  h.sources[0].error()
  await h.tick(1000)
  assert.equal(h.sources.length, 2)
})

test('offline suspends all work; a burst of wakeups starts only one recovery', async t => {
  const h = harness()
  t.after(() => h.connection.close())
  h.navigator.onLine = false
  h.fire('offline')
  await h.tick(300_000)
  assert.equal(h.probes.length, 0)
  assert.equal(h.timers.size, 0)
  h.navigator.onLine = true
  h.fire('online'); h.fire('focus'); h.fire('pageshow'); h.fire('visibilitychange')
  await settle()
  assert.equal(h.probes.length, 1)
  assert.equal(h.sources.length, 2)
  h.sources[1].open()
  h.fire('focus'); h.fire('pageshow')
  await settle()
  assert.equal(h.sources.length, 2)
})

test('foreground recovery detects elapsed time before throttled watchdog timers run', async t => {
  const h = harness()
  t.after(() => h.connection.close())
  h.sources[0].open()
  h.document.visibilityState = 'hidden'
  h.elapse(300_000)
  h.fire('visibilitychange')
  assert.equal(h.probes.length, 0)
  h.document.visibilityState = 'visible'
  h.fire('visibilitychange'); h.fire('pageshow')
  await settle()
  assert.equal(h.sources.length, 2)
  assert.equal(h.sources[0].readyState, 2)
})

test('a hanging probe is aborted after five seconds and retried with backoff', async t => {
  const h = harness(signal => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')))))
  t.after(() => h.connection.close())
  h.sources[0].error()
  await h.tick(1000)
  await h.tick(5000)
  assert.equal(h.probes[0].aborted, true)
  await h.tick(2000)
  assert.equal(h.probes.length, 2)
})

test('close aborts an in-flight probe and fences its late response and every callback', async () => {
  let finish
  const h = harness(() => new Promise(resolve => { finish = resolve }))
  const first = h.sources[0]
  first.error()
  await h.tick(1000)
  h.connection.close()
  assert.equal(h.probes[0].aborted, true)
  finish({ ok: false, status: 401 })
  first.open(); first.message('late'); first.error()
  h.fire('online'); h.fire('pageshow'); h.fire('focus')
  await h.tick(300_000)
  assert.equal(h.sources.length, 1)
  assert.equal(h.errors.length, 1)
  assert.deepEqual(h.messages, [])
  assert.equal(h.timers.size, 0)
})
