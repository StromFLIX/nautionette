import { test } from 'node:test'
import assert from 'node:assert/strict'
import { chatSettingsSnapshot, createChatSettings, LAST_CHAT_SETTINGS_KEY, reusableChatSettings } from './new-chat-settings.js'

const config = () => ({
  agent_id: 'writer', model: 'test/model', agent_set: 'coding', reasoning_effort: 'high',
  tools: [], project_ids: ['project'], packages: ['extension-revision']
})
const catalog = { agents: [{ id: 'writer' }] }
function fixture () {
  const values = new Map()
  const options = { storage: () => ({ getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) }), scope: () => 'server-a' }
  return { values, options, history: createChatSettings(options) }
}

test('new chats default to server resolution without history or in defaults mode', () => {
  const { history } = fixture()
  assert.deepEqual(history.forNewChat(catalog, 'last'), {})
  history.remember(config())
  assert.deepEqual(history.forNewChat(catalog, 'defaults'), {})
  assert.deepEqual(history.forNewChat(catalog, 'last'), config())
})

test('history survives reloads, retains null versus empty lists, and isolates backends', () => {
  const { history, options } = fixture()
  history.remember(config())
  const all = { ...config(), agent_id: null, tools: null, project_ids: [], packages: [], reasoning_effort: null }
  history.remember(all, 'server-b')
  const reloaded = createChatSettings(options)
  assert.deepEqual(reloaded.load(), config())
  assert.deepEqual(reloaded.load('server-b'), all)
  assert.equal(reloaded.load('server-c'), null)
})

test('snapshots exclude chat identity, messages and internet approval, and copy selections', () => {
  const input = { ...config(), id: 'chat-id', title: 'Private title', messages: ['private'], internet_status: 'allowed', agent_name: 'Writer' }
  const saved = chatSettingsSnapshot(input)
  assert.deepEqual(saved, config())
  saved.packages.push('another-revision')
  saved.tools.push('tool')
  assert.deepEqual(input.packages, config().packages)
  assert.deepEqual(input.tools, [])
})

test('deleted agents lose only the profile reference, not the remembered restrictions', () => {
  assert.deepEqual(reusableChatSettings({ agents: [] }, config()), { ...config(), agent_id: null })
  assert.deepEqual(reusableChatSettings(catalog, config()), config())
})

test('malformed or incomplete stored data falls back without guessing settings', () => {
  for (const value of [null, [], 'bad', {}, { ...config(), tools: false }, { ...config(), project_ids: [1] }, { ...config(), packages: {} }, { ...config(), packages: [{}] }]) {
    assert.equal(chatSettingsSnapshot(value), null)
  }
  const { history, values } = fixture()
  const key = `${LAST_CHAT_SETTINGS_KEY}:server-a`
  values.set(key, '{broken')
  assert.deepEqual(history.forNewChat(catalog, 'last'), {})
  values.set(key, JSON.stringify({ model: 'test/model' }))
  assert.deepEqual(history.forNewChat(catalog, 'last'), {})
  history.remember(config())
  assert.deepEqual(history.load(), config())
})

test('storage failures preserve session choices rather than restoring stale persisted values', () => {
  const { history, options } = fixture()
  history.remember(config())
  const errors = []
  const failing = createChatSettings({ ...options, onError: message => errors.push(message), storage: () => ({
    getItem: options.storage().getItem, setItem: () => { throw new Error('Full') }
  }) })
  const latest = { ...config(), tools: null, reasoning_effort: null }
  failing.remember(latest)
  assert.deepEqual(failing.load(), latest)
  const copy = failing.load()
  copy.packages.push('another-revision')
  assert.deepEqual(failing.load(), latest)
  assert.equal(errors.length, 1)
})

test('another tab can update history without changing an already loaded draft', () => {
  const { history, options } = fixture()
  history.remember(config())
  const draft = history.load()
  createChatSettings(options).remember({ ...config(), tools: null })
  assert.equal(history.load().tools, null)
  assert.deepEqual(draft.tools, [])
})
