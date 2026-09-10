import assert from 'node:assert/strict'
import test from 'node:test'
import { activityBreakdown, compactDuration } from './activity-timing.js'
import { isToolPending, summarizeToolCalls } from './timeline.js'

test('compact durations distinguish unmeasured values and scale without long decimals', () => {
  for (const [value, expected] of [
    [undefined, ''], [null, ''], ['20', ''], [NaN, ''], [-1, ''],
    [0, '0ms'], [172, '172ms'], [1234, '1.2s'], [12_000, '12s'],
    [60_000, '1m 0s'], [218_000, '3m 38s'], [3_661_000, '1h 1m']
  ]) assert.equal(compactDuration(value), expected)
})

test('only the active live phase ticks; final and partial measurements remain fixed', () => {
  const timing = { tools_ms: 2000, thinking_ms: 1000, reply_ms: 500, other_ms: 100,
    active: 'tools', updated_at: 10 }
  assert.deepEqual(activityBreakdown(timing, true, 11_000).map(part => part.duration), ['3s', '1s', '500ms', '100ms'])
  assert.equal(activityBreakdown(timing, false, 99_000)[0].duration, '2s')
  assert.equal(activityBreakdown({ ...timing, active: null }, true, 99_000)[0].duration, '2s')
  assert.equal(activityBreakdown(timing, true, 9_000)[0].duration, '2s')
})

test('old records and unreported thinking do not invent timings', () => {
  assert.deepEqual(activityBreakdown(null), [])
  assert.deepEqual(activityBreakdown({}), [])
  assert.deepEqual(activityBreakdown({ tools_ms: 500, thinking_ms: 0, other_ms: 20 }).map(part => part.label), ['Tools', 'Other'])
  assert.deepEqual(activityBreakdown({ tools_ms: -1, thinking_ms: '2' }), [])
})

test('group octagon and row dot agree about completed and interrupted calls', () => {
  const pending = { kind: 'tool', name: 'bash', ok: null }
  assert.equal(isToolPending(pending), true)
  for (const step of [
    { ...pending, ok: true }, { ...pending, ok: false },
    { ...pending, finished_at: 123 }, { ...pending, interrupted: true }
  ]) {
    assert.equal(isToolPending(step), false)
    assert.equal(summarizeToolCalls([step]).pending, false)
  }
})
