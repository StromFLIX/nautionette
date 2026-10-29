import { test } from 'node:test'
import assert from 'node:assert/strict'
import { commandSearchGroups, searchChats } from './command-search.js'
import { SETTINGS_SECTIONS } from './settings-registry.js'

const chats = [
  { id: 'preview', title: 'Planning', last_message: { preview: 'The desktop design review' } },
  { id: 'title', title: 'Design review', updated_at: 1 },
  { id: 'other', title: 'Shopping' }
]

test('chat search matches title and latest preview, ranks titles first and leaves the source untouched', () => {
  assert.deepEqual(searchChats(chats, ' REVIEW design ').map(chat => chat.id), ['title', 'preview'])
  assert.deepEqual(chats.map(chat => chat.id), ['preview', 'title', 'other'])
  assert.equal(searchChats(chats, 'unmatched').length, 0)
  assert.equal(searchChats([{ id: 'empty' }], 'model').length, 0)
  assert.equal(searchChats(chats, '').length, 3)
})

test('empty palette offers recent chats and every settings category', () => {
  const groups = commandSearchGroups(chats, '')
  assert.deepEqual(groups.map(group => group.key), ['chats', 'settings'])
  assert.equal(groups[1].results.length, SETTINGS_SECTIONS.length)
  assert.ok(groups[1].results.every(result => result.to.path.startsWith('/settings/')))
  assert.ok(groups[0].results.some(result => result.to.path === '/chats/title'))
})

test('settings search uses registry keywords and links to the exact setting', () => {
  const [settings] = commandSearchGroups([], 'visibility grace')
  assert.equal(settings.key, 'settings')
  assert.ok(settings.results.some(result => result.to.path === '/settings/workspace' && result.to.hash === '#chatSelectionGraceSeconds'))
  assert.equal(commandSearchGroups([], 'settings')[0].results.length, SETTINGS_SECTIONS.length)
})

test('scopes, empty states and result limits remain independent of activity filters', () => {
  assert.ok(commandSearchGroups(chats, 'Design', 'settings').every(group => group.key === 'settings'))
  assert.equal(commandSearchGroups(chats, '', 'chats').length, 1)
  assert.deepEqual(commandSearchGroups(chats, 'nothing-matches'), [])
  const many = Array.from({ length: 60 }, (_, i) => ({ id: String(i), title: `Old chat ${i}`, updated_at: 1 }))
  assert.equal(commandSearchGroups(many, '')[0].results.length, 8)
  const [group] = commandSearchGroups(many, 'old')
  assert.equal(group.results.length, 30)
  assert.equal(group.count, 60)
})
