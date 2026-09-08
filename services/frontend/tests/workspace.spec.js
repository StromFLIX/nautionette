import { test, expect } from '@playwright/test'
import { THEMES } from '../src/themes.js'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockDesign } from './design-fixture.js'

for (const theme of THEMES) {
  test(`${theme.name} keeps mobile chat controls and source code usable`, async ({ page, context }) => {
    await mockDesign(context)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await context.addInitScript(({ key, theme }) => localStorage.setItem(key, JSON.stringify({ theme, codeWrap: true })), { key: PREFERENCES_KEY, theme: theme.id })
    await page.setViewportSize({ width: 320, height: 568 })
    await page.goto('/chats/alpha')
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme.id)
    await expect(page.getByRole('button', { name: 'Select model', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Reasoning effort', exact: true })).toBeHidden()
    await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Keep this draft')
    const toggle = page.getByRole('button', { name: 'Chat configuration', exact: true })
    await toggle.press('Space')
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    for (const name of ['Select agent set', 'Reasoning effort', 'Select tools', 'Select projects']) {
      const control = page.getByRole('button', { name, exact: true })
      await expect(control).toBeVisible()
      const box = await control.boundingBox()
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width).toBeLessThanOrEqual(320)
      expect(box.y + box.height).toBeLessThanOrEqual(568)
    }
    await page.getByRole('textbox', { name: 'Message', exact: true }).focus()
    await expect(page.getByRole('tooltip')).toHaveCount(0)
    await page.screenshot({ path: `/tmp/nautionette-theme-${theme.id}-mobile.png` })
    await toggle.click()
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Keep this draft')
    await page.goto('/workflows/digest?tab=Code')
    await expect(page.locator('.viewer')).toBeVisible()
    await expect(page.locator('.viewer__text').nth(1)).toHaveCSS('white-space', 'pre-wrap')
    expect(await page.locator('.viewer__scroll').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
    expect(await page.locator('.viewer__body').evaluate(element => element.clientWidth)).toBeGreaterThan(250)
    await page.screenshot({ path: `/tmp/nautionette-theme-${theme.id}-mobile-code.png` })
    expect(errors).toEqual([])
  })
}

test.describe('touch layouts', () => {
  test.use({ hasTouch: true, isMobile: true })

  for (const width of [320, 390]) {
    test(`expanded composer stays reachable in a keyboard-sized viewport at ${width}px`, async ({ page, context }) => {
      const state = await mockDesign(context)
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      await page.setViewportSize({ width, height: 568 })
      await page.goto('/chats/alpha')
      await page.getByRole('button', { name: 'Chat configuration', exact: true }).tap()
      const input = page.getByRole('textbox', { name: 'Message', exact: true })
      const draft = Array.from({ length: 8 }, (_, index) => `Draft line ${index + 1}`).join('\n')
      await input.fill(draft)
      await page.setViewportSize({ width, height: 320 })
      const footer = page.locator('.thread__foot')
      await expect(footer).toBeInViewport({ ratio: 1 })
      for (const name of ['Select agent set', 'Reasoning effort', 'Select tools', 'Select projects', 'Send message']) {
        const control = page.getByRole('button', { name, exact: true })
        await control.scrollIntoViewIfNeeded()
        await expect(control).toBeInViewport({ ratio: 1 })
        const box = await control.boundingBox()
        expect(box.height).toBeGreaterThanOrEqual(40)
      }
      await expect(page.locator('.pane-head')).toBeInViewport({ ratio: 1 })
      await page.getByRole('button', { name: 'Send message', exact: true }).tap()
      await expect.poll(() => state.sent.length).toBe(1)
      expect(state.sent[0].text).toBe(draft)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      expect(state.writes).toEqual([])
      expect(errors).toEqual([])
    })
  }
})

test('long tooltips fit inside a narrow viewport and dismiss with Escape', async ({ page, context }) => {
  await mockDesign(context)
  await page.setViewportSize({ width: 320, height: 568 })
  await page.goto('/chats/alpha')
  await page.getByRole('button', { name: 'Chat configuration', exact: true }).click()
  await page.getByRole('button', { name: 'Reasoning effort', exact: true }).hover()
  const tooltip = page.getByRole('tooltip').filter({ hasText: 'Higher levels may take longer' })
  await expect(tooltip).toBeVisible()
  const box = await tooltip.boundingBox()
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(320)
  await page.keyboard.press('Escape')
  await expect(tooltip).toHaveCount(0)
})

test('keyboard settings shortcut and workspace reset preserve the selected theme', async ({ page, context }) => {
  const state = await mockDesign(context)
  await page.goto('/chats/alpha')
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible()
  await page.keyboard.press('Control+,')
  await expect(page).toHaveURL(/\/settings\/general$/)
  await page.getByRole('navigation', { name: 'Settings categories' }).getByRole('link', { name: 'Appearance', exact: true }).click()
  await page.getByRole('button', { name: 'Sand theme', exact: true }).click()
  await page.getByRole('navigation', { name: 'Settings categories' }).getByRole('link', { name: 'Workspace', exact: true }).click()
  await page.getByRole('switch', { name: 'Show chat configuration', exact: true }).check()
  await page.getByLabel('Density', { exact: true }).selectOption('compact')
  await page.getByRole('button', { name: 'Reset workspace', exact: true }).click()
  await expect(page.getByRole('switch', { name: 'Show chat configuration', exact: true })).not.toBeChecked()
  await expect(page.getByLabel('Density', { exact: true })).toHaveValue('comfortable')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'sand')
  expect(state.writes).toEqual([])
})

test('malformed stored preferences recover without changing instance settings', async ({ page, context }) => {
  const state = await mockDesign(context)
  await context.addInitScript(key => localStorage.setItem(key, '{broken'), PREFERENCES_KEY)
  await page.goto('/settings/appearance')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'orbit')
  await expect(page.getByRole('alert')).toContainText('Defaults are in use')
  await page.getByRole('button', { name: 'Daylight theme', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'daylight')
  await expect(page.getByRole('alert')).toHaveCount(0)
  const saved = await page.evaluate(key => JSON.parse(localStorage.getItem(key)), PREFERENCES_KEY)
  expect(saved.theme).toBe('daylight')
  expect(state.writes).toEqual([])
})

test('tool catalogs are collapsed until needed and searchable by capability', async ({ page, context }) => {
  await mockDesign(context)
  await page.goto('/settings/mcp')
  await expect(page.getByText('mail_search', { exact: true })).toBeHidden()
  await page.getByRole('searchbox', { name: 'Search tool catalog', exact: true }).fill('Search your mail')
  await expect(page.getByText('mail_search', { exact: true })).toBeVisible()
  await expect(page.getByText('mail_read', { exact: true })).toHaveCount(0)
  await page.getByRole('searchbox', { name: 'Search tool catalog', exact: true }).fill('no-match-xyz')
  await expect(page.getByText('No matching tools.', { exact: true })).toBeVisible()
})
