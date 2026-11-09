import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chatAncestors, chatForest, chatTreeRows, filterChatForest } from './chat-tree.js'

const chats = [
  { id: 'root', title: 'Build website', created_at: 1 },
  { id: 'child', title: 'Design branding', delegated_parent_chat_id: 'root', created_at: 2 },
  { id: 'grandchild', title: 'Research colours', delegated_parent_chat_id: 'child', created_at: 3, unread: true, answering: true },
  { id: 'sibling', title: 'Implement layout', delegated_parent_chat_id: 'root', created_at: 4 },
  { id: 'branch', title: 'Explore alternative', parent_chat_id: 'root', created_at: 5 }
]

test('trees collapse by default, count every descendant and aggregate hidden activity without changing chats', () => {
  const forest = chatForest(chats)
  assert.deepEqual(forest.map(node => node.chat.id), ['branch', 'root'])
  const root = forest[1]
  assert.equal(root.count, 3)
  assert.equal(root.summary.answering, true)
  assert.equal(root.summary.unread, true)
  assert.equal(root.summary.last_activity_at, 4)
  assert.equal(root.chat.answering, undefined)
  assert.equal(chatTreeRows(root, new Set()).length, 1)
  assert.deepEqual(chatTreeRows(root, new Set(['root'])).map(row => [row.chat.id, row.depth]), [
    ['root', 0], ['sibling', 1], ['child', 1]
  ])
  assert.deepEqual(chatTreeRows(root, new Set(['root', 'child'])).map(row => row.chat.id), ['root', 'sibling', 'child', 'grandchild'])
})

test('search keeps the full path and can reveal nested matches without changing expansion state', () => {
  const forest = filterChatForest(chatForest(chats), chat => chat.title.includes('colours'))
  assert.equal(forest.length, 1)
  assert.deepEqual(chatTreeRows(forest[0], new Set(), true).map(row => row.chat.id), ['root', 'child', 'grandchild'])
  assert.deepEqual(chatAncestors(chats, 'grandchild'), ['child', 'root'])
  assert.deepEqual(chatAncestors(chats, 'missing'), [])
})

test('selecting a parent retains its entire delegated tree even across agent profiles', () => {
  const forest = filterChatForest(chatForest(chats), chat => chat.id === 'root')
  assert.equal(chatTreeRows(forest[0], new Set(), true).length, 4)
})

test('deleted parents, self-links and cycles never lose chats or recurse forever', () => {
  const malformed = [
    { id: 'orphan', delegated_parent_chat_id: 'deleted' },
    { id: 'self', delegated_parent_chat_id: 'self' },
    { id: 'a', delegated_parent_chat_id: 'b' },
    { id: 'b', delegated_parent_chat_id: 'a' },
    { id: 'c', delegated_parent_chat_id: 'b' }
  ]
  const forest = chatForest(malformed)
  assert.equal(forest.length, 5)
  assert.equal(new Set(forest.flatMap(node => chatTreeRows(node, new Set(), true)).map(node => node.chat.id)).size, 5)
  assert.deepEqual(chatAncestors(malformed, 'c'), ['b', 'a'])
})
