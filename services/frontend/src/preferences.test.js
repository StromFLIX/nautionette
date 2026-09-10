import { test } from 'node:test'
import assert from 'node:assert/strict'
import { WORKSPACE_SETTINGS, preferenceDefaults, sanitizePreferences, validPreference, workspaceDefaults } from './preferences-schema.js'
import { THEME_TOKENS } from './themes.js'
import { SETTINGS_SECTIONS, searchSettings } from './settings-registry.js'

test('workspace defaults are complete, independent and valid', () => {
  const defaults = preferenceDefaults()
  assert.equal(defaults.theme, 'orbit')
  assert.equal(defaults.interfaceSize, 125)
  assert.equal(defaults.newChatSettings, 'last')
  assert.equal(defaults.motion, 'system')
  assert.deepEqual(Object.keys(workspaceDefaults()).sort(), WORKSPACE_SETTINGS.map(field => field.key).sort())
  for (const field of WORKSPACE_SETTINGS) assert.ok(validPreference(field.key, defaults[field.key]), field.key)
  defaults.overrides.orbit = { accent: '#000000' }
  assert.deepEqual(preferenceDefaults().overrides, {})
})

test('new-chat mode defaults to last settings and migrates away from persistent disclosure', () => {
  const migrated = sanitizePreferences({ composerExpanded: true })
  assert.equal(migrated.newChatSettings, 'last')
  assert.equal(Object.hasOwn(migrated, 'composerExpanded'), false)
  for (const mode of ['last', 'defaults']) {
    assert.equal(validPreference('newChatSettings', mode), true)
    assert.equal(sanitizePreferences({ newChatSettings: mode }).newChatSettings, mode)
  }
  for (const mode of [true, null, 'other', 1]) {
    assert.equal(validPreference('newChatSettings', mode), false)
    assert.equal(sanitizePreferences({ newChatSettings: mode }).newChatSettings, 'last')
  }
  assert.ok(searchSettings('new chat settings').some(entry => entry.id === 'newChatSettings'))
})

test('interface size migrates old preferences and accepts only supported sizes', () => {
  assert.equal(sanitizePreferences({ theme: 'sand', sideWidth: 400 }).interfaceSize, 125)
  for (const size of [100, 110, 125, 150]) {
    assert.equal(validPreference('interfaceSize', size), true)
    assert.equal(sanitizePreferences({ interfaceSize: size }).interfaceSize, size)
  }
  for (const size of [0, -1, 500, NaN, Infinity, '125', null, true]) {
    assert.equal(validPreference('interfaceSize', size), false)
    assert.equal(sanitizePreferences({ interfaceSize: size }).interfaceSize, 125)
  }
  assert.equal(workspaceDefaults().interfaceSize, 125)
  assert.ok(searchSettings('browser zoom').some(entry => entry.id === 'interfaceSize'))
})

test('sidebar collapse defaults to expanded and only accepts boolean preferences', () => {
  assert.equal(preferenceDefaults().sideCollapsed, false)
  assert.equal(workspaceDefaults().sideCollapsed, false)
  assert.equal(sanitizePreferences({ sideWidth: 400 }).sideCollapsed, false)
  for (const value of [true, false]) {
    assert.equal(validPreference('sideCollapsed', value), true)
    const saved = sanitizePreferences({ sideCollapsed: value, sideWidth: 400 })
    assert.equal(saved.sideCollapsed, value)
    assert.equal(saved.sideWidth, 400)
  }
  for (const value of ['true', 1, null, {}, []]) {
    assert.equal(validPreference('sideCollapsed', value), false)
    assert.equal(sanitizePreferences({ sideCollapsed: value }).sideCollapsed, false)
  }
})

test('chat visibility preferences migrate, validate, reset and contribute to settings search', () => {
  for (const defaults of [preferenceDefaults(), workspaceDefaults(), sanitizePreferences({ chatActiveMinutes: 60 })]) {
    assert.equal(defaults.chatKeepSelectedVisible, true)
    assert.equal(defaults.chatSelectionGraceSeconds, 60)
  }
  assert.equal(sanitizePreferences({ chatKeepSelectedVisible: false }).chatKeepSelectedVisible, false)
  for (const invalid of ['false', 0, null]) {
    assert.equal(validPreference('chatKeepSelectedVisible', invalid), false)
    assert.equal(sanitizePreferences({ chatKeepSelectedVisible: invalid }).chatKeepSelectedVisible, true)
  }
  for (const seconds of [0, 1, 60, 120, 3600]) {
    assert.equal(validPreference('chatSelectionGraceSeconds', seconds), true)
    assert.equal(sanitizePreferences({ chatSelectionGraceSeconds: seconds }).chatSelectionGraceSeconds, seconds)
  }
  for (const invalid of [-1, 3601, NaN, Infinity, '60', null, true]) {
    assert.equal(validPreference('chatSelectionGraceSeconds', invalid), false)
    assert.equal(sanitizePreferences({ chatSelectionGraceSeconds: invalid }).chatSelectionGraceSeconds, 60)
  }
  assert.ok(searchSettings('selected chat').some(entry => entry.id === 'chatKeepSelectedVisible'))
  assert.ok(searchSettings('grace period').some(entry => entry.id === 'chatSelectionGraceSeconds'))
})

test('activity animations default on, validate independently and are searchable', () => {
  const keys = ['chatListAnimation', 'messageWindowAnimation', 'toolIndicatorAnimation']
  for (const key of keys) {
    assert.equal(preferenceDefaults()[key], true)
    assert.equal(workspaceDefaults()[key], true)
    assert.equal(sanitizePreferences({ motion: 'reduced' })[key], true)
    assert.equal(validPreference(key, false), true)
    const saved = sanitizePreferences({ [key]: false })
    assert.equal(saved[key], false)
    for (const other of keys.filter(other => other !== key)) assert.equal(saved[other], true)
    for (const invalid of ['false', 0, null, [], {}]) {
      assert.equal(validPreference(key, invalid), false)
      assert.equal(sanitizePreferences({ [key]: invalid })[key], true)
    }
    assert.ok(searchSettings('animation').some(entry => entry.id === key))
  }
})

test('stored preferences isolate overrides by theme and drop invalid or unknown fields', () => {
  const result = sanitizePreferences({
    theme: 'daylight', overrides: { orbit: { accent: '#123456' }, daylight: { font: 'monospace', 'rail-width': 1 }, unknown: { accent: '#abcdef' } },
    density: 'compact', sideWidth: 480, motion: 'reduced', showTimestamps: false, flowDirection: 'LR',
    composerExpanded: 'yes', codeWrap: 1, sendShortcut: 'javascript:alert(1)', chatActiveMinutes: '60', credentials: 'private'
  })
  assert.equal(result.theme, 'daylight')
  assert.deepEqual(result.overrides, { orbit: { accent: '#123456' }, daylight: { font: 'monospace' } })
  assert.equal(result.sideWidth, 480)
  assert.equal(result.density, 'compact')
  assert.equal(result.showTimestamps, false)
  assert.equal(result.flowDirection, 'LR')
  assert.equal(result.composerExpanded, undefined)
  assert.equal(result.sendShortcut, 'enter')
  assert.equal(result.chatActiveMinutes, 0)
  assert.equal(result.credentials, undefined)
  for (const value of [null, false, [], 'bad']) assert.deepEqual(sanitizePreferences(value), preferenceDefaults())
  assert.equal(validPreference('sideWidth', 100000), false)
  assert.equal(validPreference('sideWidth', NaN), false)
  assert.equal(validPreference('unknown', true), false)
})

test('all preference controls and theme tokens contribute searchable settings', () => {
  assert.equal(new Set(SETTINGS_SECTIONS.map(section => section.key)).size, SETTINGS_SECTIONS.length)
  for (const section of SETTINGS_SECTIONS) {
    assert.equal(typeof section.load, 'function')
    assert.ok(section.scope && section.group && section.entries.length)
  }
  const workspace = SETTINGS_SECTIONS.find(section => section.key === 'workspace')
  assert.deepEqual(workspace.entries.map(entry => entry.id), WORKSPACE_SETTINGS.map(field => field.key))
  const appearance = SETTINGS_SECTIONS.find(section => section.key === 'appearance')
  for (const token of THEME_TOKENS) assert.ok(appearance.entries.some(entry => entry.id === `token-${token.key}`))
})

test('settings search is normalized, spans categories and preserves device/instance scope', () => {
  assert.equal(searchSettings('  ').length, 0)
  assert.equal(searchSettings('no-such-setting-xyz').length, 0)
  assert.ok(searchSettings('DARK light').some(entry => entry.id === 'themes'))
  assert.ok(searchSettings('mcp endpoint').some(entry => entry.id === 'mcp-servers'))
  assert.ok(searchSettings('--syntax-comment').some(entry => entry.id === 'token-syntax-comment'))
  assert.ok(searchSettings('font').every(entry => entry.section.key === 'appearance'))
  assert.ok(searchSettings('timezone').some(entry => entry.scope === 'Per workflow'))
  const connection = searchSettings('access token').find(entry => entry.id === 'access-token')
  assert.equal(connection.scope, 'This device')
  assert.equal(connection.section.key, 'general')
})

test('new settings contributions work without hard-coded search or navigation logic', () => {
  const sections = [...SETTINGS_SECTIONS, { key: 'future', label: 'Future connection', group: 'Connections', scope: 'Instance', entries: [
    { id: 'future-api', label: 'Atlas connection', description: 'New integration', keywords: 'API', scope: 'This device' }
  ] }]
  const [result] = searchSettings('Atlas API', sections)
  assert.equal(result.id, 'future-api')
  assert.equal(result.section.key, 'future')
  assert.equal(result.scope, 'This device')
})
