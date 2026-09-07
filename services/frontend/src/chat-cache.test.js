import 'fake-indexeddb/auto'
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createChatCache, CHAT_CACHE_LIMIT } from './chat-cache.js'

function snapshot (id, text = 'Saved answer') {
  return { chat: { id, title: id, project_ids: ['project'] }, messages: [{ id: `${id}-answer`, content: text }], active_turn: null }
}

test('100 full chats survive reopening the cache and eviction is isolated by server', async () => {
  const name = crypto.randomUUID()
  const cache = createChatCache(name)
  for (let index = 0; index < CHAT_CACHE_LIMIT; index++) await cache.put('first', snapshot(`chat-${index}`))
  await cache.put('second', snapshot('chat-0', 'Other server'))
  await cache.saveList('first', [{ id: 'chat-0', title: 'First chat' }])
  const reopened = createChatCache(name)
  for (let index = 0; index < CHAT_CACHE_LIMIT; index++) assert.deepEqual(await reopened.get('first', `chat-${index}`), snapshot(`chat-${index}`))
  assert.equal((await reopened.get('second', 'chat-0')).messages[0].content, 'Other server')
  assert.deepEqual(await reopened.list('first'), [{ id: 'chat-0', title: 'First chat' }])
  await cache.put('first', snapshot('overflow'))
  assert.equal(await cache.get('first', 'chat-0'), null)
  assert.ok(await cache.get('second', 'chat-0'))
  await cache.remove('first', 'overflow')
  assert.equal(await cache.get('first', 'overflow'), null)
})

test('slow background fetches cannot overwrite newer stream snapshots', async () => {
  const cache = createChatCache(crypto.randomUUID())
  const initial = snapshot('chat')
  await cache.put('server', initial)
  const fresh = { ...snapshot('chat', 'Fresh answer'), active_turn: { steps: [{ kind: 'text', text: 'Live progress' }] } }
  await cache.put('server', fresh)
  await cache.put('server', snapshot('chat', 'Stale answer'), initial)
  assert.deepEqual(await cache.get('server', 'chat'), fresh)
})

test('acknowledged messages persist exactly once without dropping streamed content', async () => {
  const cache = createChatCache(crypto.randomUUID())
  await cache.put('server', snapshot('chat'))
  const message = { id: 'user', chat_id: 'chat', role: 'user', content: 'Queued reply' }
  await cache.accept('server', message)
  await cache.accept('server', message)
  assert.deepEqual((await cache.get('server', 'chat')).messages, [...snapshot('chat').messages, message])
})