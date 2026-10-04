import assert from 'node:assert/strict'
import { test } from 'node:test'
import { CHAT_GROUP_OPTIONS, groupChats } from './chat-groups.js'
import { sanitizePreferences, validPreference, workspaceDefaults } from './preferences-schema.js'

const members = groups => groups.map(group => [group.key, group.chats.map(chat => chat.id)])

test('grouping offers date, activity and project, preserving All and valid saved selections', () => {
  assert.deepEqual(CHAT_GROUP_OPTIONS.map(([key]) => key), ['none', 'date', 'activity', 'project'])
  for (const [key] of CHAT_GROUP_OPTIONS) {
    assert.equal(validPreference('chatGroupBy', key), true)
    assert.equal(sanitizePreferences({ chatGroupBy: key }).chatGroupBy, key)
  }
  for (const key of ['model', 'internet', 'other']) {
    assert.equal(validPreference('chatGroupBy', key), false)
    assert.equal(sanitizePreferences({ chatGroupBy: key }).chatGroupBy, workspaceDefaults().chatGroupBy)
  }
})

test('date groups use completed activity in the local timezone, including midnight and DST boundaries', () => {
  const previous = process.env.TZ
  try {
    for (const zone of ['America/New_York', 'Europe/Berlin', 'Pacific/Auckland']) {
      process.env.TZ = zone
      const start = (day, hour, minute) => new Date(2025, 2, day, hour, minute).getTime() / 1000
      const chats = [
        { id: 'before-midnight', created_at: start(8, 23, 59), updated_at: start(10, 12, 0) },
        { id: 'after-midnight', created_at: start(8, 12, 0), last_activity_at: start(9, 0, 1) },
        { id: 'after-dst', created_at: start(8, 13, 0), last_activity_at: start(9, 4, 0) },
        { id: 'next-day', created_at: start(10, 0, 0) },
        { id: 'missing', updated_at: start(10, 13, 0) }
      ]
      assert.deepEqual(members(groupChats(chats, 'date')), [
        ['2025-3-10', ['next-day']],
        ['2025-3-9', ['after-dst', 'after-midnight']],
        ['2025-3-8', ['before-midnight']],
        ['__unknown_date__', ['missing']]
      ], zone)
      assert.equal(groupChats(chats, 'date')[3].label, 'Unknown date')
      chats[0].last_activity_at = start(10, 12, 0)
      assert.deepEqual(members(groupChats(chats, 'date')), [
        ['2025-3-10', ['before-midnight', 'next-day']],
        ['2025-3-9', ['after-dst', 'after-midnight']],
        ['__unknown_date__', ['missing']]
      ], zone)
    }
  } finally {
    if (previous === undefined) delete process.env.TZ
    else process.env.TZ = previous
  }
})

test('activity groups have explicit priority and sort by completed activity within each state', () => {
  const chats = [
    { id: 'inactive', created_at: 100 },
    { id: 'unread', created_at: 90, unread: true },
    { id: 'working-old', created_at: 20, updated_at: 200, answering: true, unread: true },
    { id: 'working-new', created_at: 10, last_activity_at: 30, answering: true }
  ]
  const groups = groupChats(chats, 'activity')
  assert.deepEqual(groups.map(group => group.label), ['Active', 'Unread', 'Inactive'])
  assert.deepEqual(members(groups), [
    ['active', ['working-new', 'working-old']], ['unread', ['unread']], ['inactive', ['inactive']]
  ])
  Object.assign(chats[2], { answering: false, unread: false })
  assert.deepEqual(members(groupChats(chats, 'activity'))[2], ['inactive', ['inactive', 'working-old']])
})

test('project groups use newest completed activity, include multi-project chats once per group, and keep No project last', () => {
  const chats = [
    { id: 'old', created_at: 10, updated_at: 300, project_ids: ['a'] },
    { id: 'shared', created_at: 20, project_ids: ['b', 'a', 'a'] },
    { id: 'new', created_at: 5, last_activity_at: 30, project_ids: ['b'] },
    { id: 'unassigned', created_at: 40 }
  ]
  const projects = [{ id: 'a', full_name: 'Org/A' }, { id: 'b', full_name: 'Org/B' }]
  const groups = groupChats(chats, 'project', projects)
  assert.deepEqual(groups.map(group => group.label), ['Org/B', 'Org/A', 'No project'])
  assert.deepEqual(members(groups), [['b', ['new', 'shared']], ['a', ['shared', 'old']], ['__none__', ['unassigned']]])
  const old = chats[0]
  old.updated_at += 1000
  assert.deepEqual(members(groupChats(chats.reverse(), 'project', projects)), members(groups))
  old.last_activity_at = 50
  assert.deepEqual(members(groupChats(chats, 'project', projects)), [
    ['a', ['old', 'shared']], ['b', ['new', 'shared']], ['__none__', ['unassigned']]
  ])
  assert.equal(groupChats([{ id: 'missing', project_ids: ['removed-project'] }], 'project')[0].label, 'removed-project')
})

test('All uses the same stable sort without mutating the input or hiding older chats', () => {
  const chats = [{ id: 'old', created_at: 10, answering: true }, { id: 'new', created_at: 20, updated_at: 20 }]
  assert.deepEqual(members(groupChats(chats, 'none')), [['__all__', ['new', 'old']]])
  assert.deepEqual(chats.map(chat => chat.id), ['old', 'new'])
  assert.deepEqual(groupChats([], 'date'), [])
})
