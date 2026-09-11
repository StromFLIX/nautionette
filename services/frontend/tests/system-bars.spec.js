import { test, expect } from '@playwright/test'
import { THEMES, resolveTheme } from '../src/themes.js'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockDesign } from './design-fixture.js'

async function mockAndroid (context, { theme = 'orbit', fail = false } = {}) {
  await mockDesign(context)
  await context.addInitScript(({ theme, fail, key }) => {
    // Only the app has native APIs/storage; sandboxed skin previews do not.
    if (window !== window.top) return
    localStorage.setItem('nautionette.server', location.origin)
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ theme }))
    window.systemBarCalls = []
    window.failSystemBars = fail
    window.androidBridge = {}
    window.Capacitor = {
      isNativePlatform: () => true,
      PluginHeaders: [
        { name: 'NautionetteSystemBars', methods: [{ name: 'setAppearance', rtype: 'promise' }] },
        { name: 'Keyboard', methods: [{ name: 'addListener', rtype: 'callback' }] }
      ],
      nativeCallback: () => 'keyboard-listener',
      nativePromise: async (plugin, method, options) => {
        if (plugin !== 'NautionetteSystemBars') throw new Error(`Unexpected plugin: ${plugin}`)
        window.systemBarCalls.push({ method, ...options })
        if (window.failSystemBars) throw new Error('Native bridge unavailable')
      }
    }
  }, { theme, fail, key: PREFERENCES_KEY })
}

const lastCall = page => page.evaluate(() => window.systemBarCalls?.at(-1))
const appearance = (status, navigation, darkIcons) => ({
  method: 'setAppearance', statusBarColor: status, navigationBarColor: navigation,
  darkStatusBarIcons: darkIcons, darkNavigationBarIcons: darkIcons
})

for (const theme of THEMES) {
  test(`Android bars restore ${theme.name} and follow navigation`, async ({ page, context }) => {
    await mockAndroid(context, { theme: theme.id })
    await page.setViewportSize({ width: 412, height: 900 })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    const tokens = resolveTheme(theme.id)
    const darkIcons = theme.mode === 'light'
    await page.goto('/chats')
    await expect.poll(() => lastCall(page)).toEqual(appearance(tokens['surface-panel'], tokens['surface-rail'], darkIcons))
    await page.getByText('Design review', { exact: true }).click()
    await expect(page).toHaveURL(/\/chats\/alpha$/)
    await expect.poll(() => lastCall(page)).toEqual(appearance(tokens['surface-app'], tokens['surface-app'], darkIcons))
    await page.getByRole('button', { name: 'Back to chats', exact: true }).click()
    await expect(page).toHaveURL(/\/chats$/)
    await expect.poll(() => lastCall(page)).toEqual(appearance(tokens['surface-panel'], tokens['surface-rail'], darkIcons))
    await page.setViewportSize({ width: 1100, height: 900 })
    await expect.poll(() => lastCall(page)).toEqual(appearance(tokens['surface-app'], tokens['surface-app'], darkIcons))
    await page.setViewportSize({ width: 412, height: 900 })
    await expect.poll(() => lastCall(page)).toEqual(appearance(tokens['surface-panel'], tokens['surface-rail'], darkIcons))
    expect(errors).toEqual([])
  })
}

test('Android bars follow live theme edits, reset, reload and cross-tab changes', async ({ page, context }) => {
  await mockAndroid(context)
  await page.setViewportSize({ width: 412, height: 900 })
  await page.goto('/settings/appearance#token-surface-app')
  await expect.poll(() => lastCall(page)).toEqual(appearance('#101517', '#101517', false))
  const canvas = page.getByLabel('Canvas', { exact: true })
  await canvas.fill('#ffffff')
  await canvas.press('Tab')
  await expect.poll(() => lastCall(page)).toEqual(appearance('#ffffff', '#ffffff', true))
  await page.reload()
  await expect.poll(() => lastCall(page)).toEqual(appearance('#ffffff', '#ffffff', true))
  await page.getByRole('button', { name: 'Reset Canvas', exact: true }).click()
  await expect.poll(() => lastCall(page)).toEqual(appearance('#101517', '#101517', false))
  await page.getByRole('button', { name: 'Sand theme', exact: true }).click()
  await expect.poll(() => lastCall(page)).toEqual(appearance('#f7f4ee', '#f7f4ee', true))
  const second = await context.newPage()
  await second.goto('/settings/appearance')
  await second.getByRole('button', { name: 'Nebula theme', exact: true }).click()
  await expect.poll(() => lastCall(page)).toEqual(appearance('#14121d', '#14121d', false))
  await second.close()
})

test('native bridge failures do not break customization and subsequent changes retry', async ({ page, context }) => {
  await mockAndroid(context, { fail: true })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/settings/appearance')
  await expect.poll(() => lastCall(page)).toEqual(appearance('#101517', '#101517', false))
  await page.evaluate(() => { window.failSystemBars = false })
  await page.getByRole('button', { name: 'Daylight theme', exact: true }).click()
  await expect.poll(() => lastCall(page)).toEqual(appearance('#f8fafc', '#f8fafc', true))
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'daylight')
  expect(errors).toEqual([])
})
