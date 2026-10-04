import assert from 'node:assert/strict'
import { test } from 'node:test'
import { byChatActivity, chatActivityAt } from './chat-order.js'
import { sanitizePreferences, validPreference, workspaceDefaults } from './preferences-schema.js'
import { searchSettings } from './settings-registry.js'

const orderedIds = chats => [...chats].sort(byChatActivity).map(chat => chat.id)

test('chats sort by completed activity, ignoring progress and read state', () => {
  const chats = [
    { id: 'working', created_at: 10, last_activity_at: 20, updated_at: 60, answering: true },
    { id: 'answered', created_at: 20, last_activity_at: 50, unread: true },
    { id: 'asked', created_at: 30, last_activity_at: 45 },
    { id: 'empty', created_at: 40 }
  ]
  assert.deepEqual(orderedIds(chats), ['answered', 'asked', 'empty', 'working'])
  const working = chats[0]
  Object.assign(working, { updated_at: 1000, last_message_at: 1000, last_user_message_at: 1000, unread: true })
  assert.deepEqual(orderedIds(chats.reverse()), ['answered', 'asked', 'empty', 'working'])
  assert.equal(chatActivityAt(working), 20)
  Object.assign(working, { answering: false, last_activity_at: 1001 })
  assert.deepEqual(orderedIds(chats), ['working', 'answered', 'asked', 'empty'])
  assert.equal(chatActivityAt(working), 1001)
  const asked = chats.find(chat => chat.id === 'asked')
  Object.assign(asked, { last_activity_at: 1002, answering: true })
  assert.deepEqual(orderedIds(chats), ['asked', 'working', 'answered', 'empty'])
})

test('equal or missing activity times are deterministic and only fall back to creation', () => {
  const chats = [{ id: 'b', created_at: 1, last_activity_at: 10 }, { id: 'a', created_at: 10 }, { id: 'd', updated_at: 100 }, { id: 'c' }]
  assert.deepEqual(orderedIds(chats), ['a', 'b', 'c', 'd'])
  chats[2].updated_at += 1000
  assert.deepEqual(orderedIds(chats.reverse()), ['a', 'b', 'c', 'd'])
  for (const value of [undefined, null, 0, -1, NaN, Infinity, '10']) {
    assert.equal(chatActivityAt({ last_activity_at: value, created_at: 10, updated_at: 20 }), 10)
    assert.equal(chatActivityAt({ last_activity_at: value, created_at: value, updated_at: 20, last_message_at: 30 }), 0)
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
