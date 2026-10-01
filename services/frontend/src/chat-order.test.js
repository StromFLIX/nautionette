import assert from 'node:assert/strict'
import { test } from 'node:test'
import { byChatStart, chatStartedAt } from './chat-order.js'
import { sanitizePreferences, validPreference, workspaceDefaults } from './preferences-schema.js'
import { searchSettings } from './settings-registry.js'

const orderedIds = chats => [...chats].sort(byChatStart).map(chat => chat.id)

test('chats sort newest-started first, regardless of messages, progress or read state', () => {
  const chats = [
    { id: 'working', created_at: 10, updated_at: 60, last_message_at: 20, last_user_message_at: 10, answering: true },
    { id: 'answered', created_at: 20, updated_at: 50, last_message_at: 50, last_user_message_at: 15, unread: true },
    { id: 'asked', created_at: 30, updated_at: 40, last_message_at: 40, last_user_message_at: 40 },
    { id: 'empty', created_at: 40 }
  ]
  assert.deepEqual(orderedIds(chats), ['empty', 'asked', 'answered', 'working'])
  Object.assign(chats[0], { updated_at: 1000, last_message_at: 1000, last_user_message_at: 1000, unread: true })
  assert.deepEqual(orderedIds(chats.reverse()), ['empty', 'asked', 'answered', 'working'])
  assert.equal(chatStartedAt(chats.find(chat => chat.id === 'working')), 10)
})

test('equal or missing start times are deterministic even if the API changes order', () => {
  const chats = [{ id: 'b', created_at: 10 }, { id: 'a', created_at: 10 }, { id: 'd', updated_at: 100 }, { id: 'c' }]
  assert.deepEqual(orderedIds(chats), ['a', 'b', 'c', 'd'])
  chats[2].updated_at += 1000
  assert.deepEqual(orderedIds(chats.reverse()), ['a', 'b', 'c', 'd'])
  for (const value of [undefined, null, 0, -1, NaN, Infinity, '10']) {
    assert.equal(chatStartedAt({ created_at: value, updated_at: 20, last_message_at: 30 }), 0)
  }
})

test('obsolete activity ordering preferences cannot restore jumping rows', () => {
  assert.equal(Object.hasOwn(workspaceDefaults(), 'chatOrderBy'), false)
  for (const mode of ['activity', 'messages', 'user', 'invalid']) {
    assert.equal(validPreference('chatOrderBy', mode), false)
    assert.equal(Object.hasOwn(sanitizePreferences({ chatOrderBy: mode }), 'chatOrderBy'), false)
  }
  assert.equal(searchSettings('chat order').some(entry => entry.id === 'chatOrderBy'), false)
})
