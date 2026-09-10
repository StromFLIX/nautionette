import { test } from 'node:test'
import assert from 'node:assert/strict'
import { computed, effectScope, reactive, ref } from 'vue'
import { useChatActivity } from './chat-activity.js'
import { workspaceDefaults } from './preferences-schema.js'

const start = 1_800_000_000_000
const oldChat = (id = 'alpha') => ({ id, updated_at: start / 1000 - 7200 })

function setup (t, overrides = {}) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout', 'setInterval'], now: start })
  const preferences = reactive({ ...workspaceDefaults(), chatActiveMinutes: 60, ...overrides })
  const selected = ref('')
  const scope = effectScope()
  const isActive = scope.run(() => useChatActivity(selected, preferences))
  t.after(() => scope.stop())
  return { selected, preferences, isActive, scope, tick: ms => t.mock.timers.tick(ms) }
}

test('an old selected chat stays active after it is read, including without a timestamp', t => {
  const { selected, isActive, tick } = setup(t)
  const chat = reactive({ ...oldChat(), unread: true })
  const visible = computed(() => isActive(chat))
  assert.equal(visible.value, true)
  selected.value = chat.id
  chat.unread = false
  tick(3_600_000)
  assert.equal(visible.value, true)
  assert.equal(isActive({ id: chat.id }), true)
  assert.equal(chat.updated_at, start / 1000 - 7200)
  assert.equal(chat.unread, false)
})

test('grace starts on deselection and expires reactively at the exact deadline', t => {
  const { selected, isActive, tick } = setup(t)
  const visible = computed(() => isActive(oldChat()))
  selected.value = 'alpha'
  tick(17_123) // A deadline that does not align with the 30-second activity tick.
  selected.value = ''
  tick(59_999)
  assert.equal(visible.value, true)
  tick(1)
  assert.equal(visible.value, false)
})

test('rapid switching retains multiple chats and reselecting starts a fresh grace period on leaving', t => {
  const { selected, isActive, tick } = setup(t)
  selected.value = 'alpha'
  selected.value = 'beta'
  tick(20_000)
  selected.value = 'alpha'
  tick(50_000) // Alpha's original expiry cannot hide the now-selected chat.
  assert.equal(isActive(oldChat('alpha')), true)
  assert.equal(isActive(oldChat('beta')), true)
  selected.value = ''
  tick(10_000)
  assert.equal(isActive(oldChat('alpha')), true)
  assert.equal(isActive(oldChat('beta')), false)
  tick(50_000)
  assert.equal(isActive(oldChat('alpha')), false)
})

test('zero grace retains only the selection; disabling retention restores the previous filter', t => {
  const { selected, preferences, isActive } = setup(t, { chatSelectionGraceSeconds: 0 })
  selected.value = 'alpha'
  assert.equal(isActive(oldChat()), true)
  selected.value = ''
  assert.equal(isActive(oldChat()), false)
  selected.value = 'alpha'
  preferences.chatKeepSelectedVisible = false
  assert.equal(isActive(oldChat()), false)
  preferences.chatKeepSelectedVisible = true
  assert.equal(isActive(oldChat()), true)
})

test('settings changes update pending deadlines and disabling clears previous holds', t => {
  const { selected, preferences, isActive, tick } = setup(t)
  selected.value = 'alpha'
  selected.value = ''
  tick(20_000)
  preferences.chatSelectionGraceSeconds = 90
  tick(69_999)
  assert.equal(isActive(oldChat()), true)
  tick(1)
  assert.equal(isActive(oldChat()), false)
  selected.value = 'alpha'
  selected.value = ''
  tick(10_000)
  preferences.chatSelectionGraceSeconds = 5
  assert.equal(isActive(oldChat()), false)
  selected.value = 'alpha'
  selected.value = ''
  preferences.chatKeepSelectedVisible = false
  preferences.chatKeepSelectedVisible = true
  assert.equal(isActive(oldChat()), false)
})

test('unread, running, approval and ordinary time-window rules remain independent of retention', t => {
  const { selected, preferences, isActive, tick } = setup(t)
  selected.value = 'alpha'
  selected.value = ''
  tick(60_000)
  for (const flags of [{ unread: true }, { answering: true }, { internet_status: 'pending' }, { internet_status: 'deciding' }]) {
    assert.equal(isActive({ ...oldChat(), ...flags }), true)
    preferences.chatKeepSelectedVisible = false
    assert.equal(isActive({ ...oldChat(), ...flags }), true)
  }
  assert.equal(isActive(oldChat()), false)
  assert.equal(isActive({ id: 'missing' }), false)
  const recent = { id: 'recent', updated_at: (start + 60_000) / 1000 }
  const visible = computed(() => isActive(recent))
  assert.equal(visible.value, true)
  tick(3_630_000)
  assert.equal(visible.value, false)
  preferences.chatActiveMinutes = 0
  assert.equal(isActive(oldChat()), true)
  assert.equal(isActive({ id: 'missing' }), true)
})

test('disposing the owner clears activity and grace timers', t => {
  const { selected, isActive, scope, tick } = setup(t)
  selected.value = 'alpha'
  selected.value = ''
  const visible = computed(() => isActive(oldChat()))
  assert.equal(visible.value, true)
  scope.stop()
  tick(120_000)
  // A disposed scope must not continue changing state in the background.
  assert.equal(visible.value, true)
})
