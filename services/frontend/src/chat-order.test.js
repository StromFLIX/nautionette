import assert from 'node:assert/strict'
import { test } from 'node:test'
import { chatRecency } from './chat-order.js'
import { sanitizePreferences, validPreference, workspaceDefaults } from './preferences-schema.js'
import { searchSettings } from './settings-registry.js'

const chats = [
  { id: 'working', updated_at: 60, last_message_at: 20, last_user_message_at: 10 },
  { id: 'answered', updated_at: 50, last_message_at: 50, last_user_message_at: 15 },
  { id: 'asked', updated_at: 40, last_message_at: 40, last_user_message_at: 40 }
]

test('chat order can count all activity, all messages, or only user messages', () => {
  const order = mode => [...chats].sort((a, b) => chatRecency(b, mode) - chatRecency(a, mode)).map(chat => chat.id)
  assert.deepEqual(order(), ['working', 'answered', 'asked'])
  assert.deepEqual(order('messages'), ['answered', 'asked', 'working'])
  assert.deepEqual(order('user'), ['asked', 'answered', 'working'])
})

test('old cached lists and empty chats have a usable ordering timestamp', () => {
  for (const mode of ['activity', 'messages', 'user']) {
    assert.equal(chatRecency({ updated_at: 20 }, mode), 20)
    assert.equal(chatRecency({ created_at: 10 }, mode), 10)
    assert.equal(chatRecency({}, mode), 0)
  }
  assert.equal(chatRecency({ updated_at: 20, last_user_message_at: 0 }, 'user'), 0)
})

test('chat order defaults to all activity and is validated, persisted and searchable', () => {
  assert.equal(workspaceDefaults().chatOrderBy, 'activity')
  assert.equal(sanitizePreferences({}).chatOrderBy, 'activity')
  assert.equal(sanitizePreferences({ chatOrderBy: 'invalid' }).chatOrderBy, 'activity')
  for (const mode of ['activity', 'messages', 'user']) {
    assert.equal(validPreference('chatOrderBy', mode), true)
    assert.equal(sanitizePreferences({ chatOrderBy: mode }).chatOrderBy, mode)
  }
  assert.ok(searchSettings('chat order').some(entry => entry.id === 'chatOrderBy' && entry.section.key === 'workspace'))
})
