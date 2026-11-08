import { test } from 'node:test'
import assert from 'node:assert/strict'
import returns from '../desktop/oauth-return.cjs'
const { createAppReturns, createFocusOwner } = returns

test('focus-only links return to the exact owner once, never receive OAuth parameters', () => {
  const focused = [], registry = createAppReturns({ focus: owner => { focused.push(owner); return true } })
  const a = registry.issue(17), b = registry.issue(29)
  assert.match(a, /^nautionette:\/\/oauth-return\/[a-f0-9]{64}$/)
  for (const value of [a + '?code=secret', a + '#state=secret', a.replace('oauth-return', 'mcp'),
    a.replace('nautionette:', 'https:'), 'nautionette://oauth-return/' + 'f'.repeat(64)]) {
    assert.equal(registry.activate(value), false)
  }
  assert.deepEqual(focused, [])
  assert.equal(registry.activate(b), true)
  assert.deepEqual(focused, [29])
  assert.equal(registry.activate(b), false)
  registry.clear(17)
  assert.equal(registry.activate(a), false)
})

test('return links expire and a new callback invalidates the previous owner link', () => {
  let time = 1000
  const registry = createAppReturns({ focus: () => true, now: () => time, lifetime: 100 })
  const first = registry.issue(1), second = registry.issue(1)
  assert.equal(registry.activate(first), false)
  time += 101
  assert.equal(registry.activate(second), false)
  const third = registry.issue(1)
  registry.close()
  assert.equal(registry.activate(third), false)
})

test('native return restores/focuses only the owning trusted window', () => {
  const calls = []
  const window = owner => ({ isDestroyed: () => false, isMinimized: () => true,
    isFocused: () => true, restore: () => calls.push([owner, 'restore']),
    show: () => calls.push([owner, 'show']), focus: () => calls.push([owner, 'focus']),
    webContents: { id: owner, isDestroyed: () => false, getURL: () => 'capacitor://localhost/' } })
  const a = window(1), b = window(2)
  const focus = createFocusOwner({ BrowserWindow: { getAllWindows: () => [a, b] },
    app: { focus: options => calls.push(['app', options]) }, platform: 'darwin' })
  assert.equal(focus(2), true)
  assert.deepEqual(calls, [[2, 'restore'], [2, 'show'], ['app', { steal: true }], [2, 'focus']])
  b.webContents.getURL = () => 'https://untrusted.test/'
  calls.length = 0
  assert.equal(focus(2), false)
  assert.deepEqual(calls, [])
})
