import assert from 'node:assert/strict'
import test from 'node:test'
import { createChatControl } from '../../images/pi-base/chat-control.mjs'

test('steering is idempotent and consumed in order at user-message boundaries', async () => {
  const sent = []
  const events = []
  const control = createChatControl({ send: (event) => sent.push(event), emit: (event) => events.push(event) })
  control.receive({ type: 'message_start', message: { role: 'user' } })
  const first = control.command({ id: 'first', type: 'steer', text: 'Do this next' })
  assert.equal(control.command({ id: 'first', type: 'steer', text: 'Do this next' }), first)
  const second = control.command({ id: 'second', type: 'steer', text: 'Then this' })
  assert.deepEqual(sent.map((event) => event.type), ['steer', 'steer'])
  control.receive({ type: 'response', id: 'first', success: true })
  control.receive({ type: 'response', id: 'second', success: true })
  assert.deepEqual(await first, { ok: true })
  assert.deepEqual(await second, { ok: true })
  assert.deepEqual(events, [])
  control.receive({ type: 'tool_execution_end' })
  control.receive({ type: 'message_start', message: { role: 'user' } })
  control.receive({ type: 'message_start', message: { role: 'user' } })
  assert.deepEqual(events, [{ type: 'input_consumed', id: 'first' }, { type: 'input_consumed', id: 'second' }])
})

test('stop clears queued input before abort and refuses new steering', async () => {
  const sent = []
  const events = []
  const control = createChatControl({ send: (event) => sent.push(event), emit: (event) => events.push(event) })
  const stopped = control.command({ id: 'stop', type: 'stop' })
  assert.deepEqual(sent, [{ type: 'clear_queue' }, { id: 'stop', type: 'abort' }])
  control.receive({ type: 'response', id: 'stop', success: true })
  assert.deepEqual(await stopped, { ok: true })
  assert.equal((await control.command({ id: 'later', type: 'steer', text: 'Later' })).ok, false)
  assert.deepEqual(events, [{ type: 'interrupted' }])
})

test('rejection and agent exit leave input unconsumed', async () => {
  const events = []
  const control = createChatControl({ send: () => {}, emit: (event) => events.push(event) })
  const rejected = control.command({ id: 'bad', type: 'steer', text: 'Rejected' })
  control.receive({ type: 'response', id: 'bad', success: false, error: 'Invalid prompt' })
  assert.equal((await rejected).ok, false)
  const pending = control.command({ id: 'pending', type: 'steer', text: 'Pending' })
  control.close()
  assert.equal((await pending).ok, false)
  assert.deepEqual(events, [])
})

test('native low-level endings leave steering and Stop available during retries', async () => {
  const sent = [], events = []
  const control = createChatControl({ send: event => sent.push(event), emit: event => events.push(event) })
  control.receive({ type: 'message_start', message: { role: 'user' } })
  control.receive({ type: 'agent_end' })
  const next = control.command({ id: 'next', type: 'steer', text: 'Next instruction' })
  control.receive({ type: 'response', id: 'next', success: true })
  assert.equal((await next).ok, true)
  control.receive({ type: 'message_start', message: { role: 'user' } })
  assert.deepEqual(events, [{ type: 'input_consumed', id: 'next' }])
  control.receive({ type: 'agent_end' })
  const stop = control.command({ id: 'stop', type: 'stop' })
  control.receive({ type: 'response', id: 'stop', success: true })
  assert.equal((await stop).ok, true)
  assert.deepEqual(sent.map(event => event.type), ['steer', 'clear_queue', 'abort'])
})

test('internal recovery cannot consume a queued user message; held steering can be retried', async () => {
  const sent = [], events = []
  const control = createChatControl({ send: event => sent.push(event), emit: event => events.push(event) })
  control.receive({ type: 'message_start', message: { role: 'user' } })
  assert.equal(control.beginRecovery(), true)
  assert.equal(control.beginRecovery(), false)
  assert.equal((await control.command({ id: 'next', type: 'steer', text: 'A real user input' })).ok, false)
  assert.equal(control.continueRecovery('internal', 'Review and continue'), true)
  control.receive({ type: 'message_start', message: { role: 'user', content: 'Review and continue' } })
  assert.deepEqual(events, [])
  const next = control.command({ id: 'next', type: 'steer', text: 'A real user input' })
  control.receive({ type: 'response', id: 'next', success: true })
  assert.equal((await next).ok, true)
  control.receive({ type: 'message_start', message: { role: 'user' } })
  assert.deepEqual(events, [{ type: 'input_consumed', id: 'next' }])
  assert.deepEqual(sent.map(event => event.type), ['prompt', 'steer'])
})

test('unconfirmed user input and Stop both prevent a new automatic continuation', async () => {
  const control = createChatControl({ send () {}, emit () {} })
  control.receive({ type: 'message_start', message: { role: 'user' } })
  const next = control.command({ id: 'next', type: 'steer', text: 'Next' })
  assert.equal(control.beginRecovery(), false)
  control.receive({ type: 'response', id: 'next', success: true })
  assert.equal((await next).ok, true)
  assert.equal(control.beginRecovery(), false)
  control.receive({ type: 'message_start', message: { role: 'user' } })
  assert.equal(control.beginRecovery(), true)
  const stop = control.command({ id: 'stop', type: 'stop' })
  control.receive({ type: 'response', id: 'stop', success: true })
  await stop
  assert.equal(control.continueRecovery('internal', 'Continue'), false)
  control.cancelRecovery()
  assert.equal(control.beginRecovery(), false)
})