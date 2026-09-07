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
  control.receive({ type: 'agent_end' })
  assert.equal((await pending).ok, false)
  assert.deepEqual(events, [])
})