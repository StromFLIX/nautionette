import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createOutbox } from './outbox.js'

function setup (send) {
  const data = new Map()
  const storage = {
    get length () { return data.size },
    key: (index) => [...data.keys()][index],
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key)
  }
  let clock = 1000
  let counter = 0
  const options = { storage, prefix: 'server:', send, now: () => clock, uuid: () => `message-${++counter}` }
  return { options, outbox: createOutbox(options), advance: () => { clock += 31000 } }
}

test('lost acknowledgements retry with the same ID after reload', async () => {
  const attempts = []
  const state = setup(async (item) => {
    attempts.push(item.id)
    if (attempts.length === 1) throw new TypeError('offline')
    return { message: { id: item.id } }
  })
  const item = state.outbox.enqueue('chat-a', 'hello')
  await state.outbox.flush()
  assert.equal(state.outbox.items()[0].error, '')
  await state.outbox.flush()
  assert.equal(attempts.length, 1)
  state.advance()
  const reloaded = createOutbox(state.options)
  await reloaded.flush()
  assert.deepEqual(attempts, [item.id, item.id])
  assert.equal(reloaded.items().length, 0)
})

test('one blocked conversation does not block another', async () => {
  let release
  const sent = []
  const state = setup(async (item) => {
    sent.push(item.chatId)
    if (item.chatId === 'chat-a') await new Promise((resolve) => { release = resolve })
    return { message: { id: item.id } }
  })
  state.outbox.enqueue('chat-a', 'first')
  state.outbox.enqueue('chat-a', 'second')
  state.outbox.enqueue('chat-b', 'independent')
  const work = state.outbox.flush()
  assert.deepEqual(sent, ['chat-a', 'chat-b'])
  release()
  await work
  assert.equal(state.outbox.items().length, 1)
})

test('permanent failures remain available to retry or discard', async () => {
  const state = setup(async () => { throw Object.assign(new Error('chat not found'), { status: 404 }) })
  const item = state.outbox.enqueue('chat-a', 'hello')
  await state.outbox.flush()
  assert.equal(state.outbox.items()[0].error, 'chat not found')
  state.outbox.discard(item.id)
  assert.equal(state.outbox.items().length, 0)
})

test('server snapshots reconcile a send even when its acknowledgement was lost', async () => {
  const state = setup(async () => { throw new TypeError('offline') })
  const item = state.outbox.enqueue('chat-a', 'hello')
  await state.outbox.flush()
  state.outbox.reconcile([{ id: item.id }])
  assert.equal(state.outbox.items().length, 0)
})

test('outboxes from different instances cannot deliver each other\'s messages', async () => {
  const state = setup(async (item) => ({ message: item }))
  state.outbox.enqueue('chat-a', 'private')
  const other = createOutbox({ ...state.options, prefix: 'other:' })
  assert.equal(other.items().length, 0)
  await other.flush()
  assert.equal(state.outbox.items().length, 1)
})

test('messages queued in the same millisecond retain their order after reload', async () => {
  const sent = []
  const state = setup(async (item) => {
    sent.push(item.text)
    return { message: { id: item.id } }
  })
  const ids = ['z-last-alphabetically', 'a-first-alphabetically']
  const outbox = createOutbox({ ...state.options, uuid: () => ids.shift() })
  outbox.enqueue('chat-a', 'first')
  outbox.enqueue('chat-a', 'second')
  const reloaded = createOutbox(state.options)
  await reloaded.flush()
  await reloaded.flush()
  assert.deepEqual(sent, ['first', 'second'])
})