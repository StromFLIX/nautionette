import { test } from 'node:test'
import assert from 'node:assert/strict'
import { THEMES, resolveTheme } from './themes.js'
import { syncSystemBars, systemBarAppearance } from './system-bars.js'

for (const theme of THEMES) {
  test(`${theme.name} system bars match the mobile panel and navigation surfaces`, () => {
    const tokens = resolveTheme(theme.id)
    assert.deepEqual(systemBarAppearance(theme.id, tokens, true), {
      statusBarColor: tokens['surface-panel'],
      navigationBarColor: tokens['surface-rail'],
      darkStatusBarIcons: theme.mode === 'light',
      darkNavigationBarIcons: theme.mode === 'light'
    })
  })

  test(`${theme.name} detail/settings system bars match the canvas`, () => {
    const tokens = resolveTheme(theme.id)
    const appearance = systemBarAppearance(theme.id, tokens, false)
    assert.equal(appearance.statusBarColor, tokens['surface-app'])
    assert.equal(appearance.navigationBarColor, tokens['surface-app'])
  })
}

test('custom surfaces choose icon contrast independently of the preset mode', () => {
  const tokens = resolveTheme('orbit', { 'surface-panel': '#ffffff', 'surface-rail': '#000000' })
  assert.deepEqual(systemBarAppearance('orbit', tokens, true), {
    statusBarColor: '#ffffff', navigationBarColor: '#000000',
    darkStatusBarIcons: true, darkNavigationBarIcons: false
  })
})

test('CSS alpha colors become opaque RGB rather than Android ARGB', () => {
  const tokens = resolveTheme('orbit', { 'surface-panel': '#ffffff00', 'surface-rail': '#ffffff80' })
  const appearance = systemBarAppearance('orbit', tokens, true)
  assert.equal(appearance.statusBarColor, '#151c1f')
  assert.equal(appearance.navigationBarColor, '#868889')
  assert.match(appearance.navigationBarColor, /^#[a-f0-9]{6}$/)
})

test('browsers do not call an unavailable native plugin', () => {
  assert.equal(syncSystemBars('orbit', resolveTheme('orbit'), true), undefined)
})
