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
    for (const name of ['Select agent', 'Select agent set', 'Reasoning effort', 'Select tools', 'Select projects']) {
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

for (const { name, width, hasTouch } of [
  { name: 'desktop', width: 1440, hasTouch: false },
  { name: 'narrow', width: 320, hasTouch: false },
  { name: 'touch', width: 390, hasTouch: true },
  { name: 'narrow touch', width: 320, hasTouch: true }
]) {
  test.describe(`${name} composer button sizes`, () => {
    test.use({ viewport: { width, height: 1000 }, hasTouch, isMobile: hasTouch })

    for (const interfaceSize of [100, 125, 150]) {
      test(`actions match chat configuration at ${interfaceSize}% scale`, async ({ page, context }) => {
        const state = await mockDesign(context)
        await context.addInitScript(({ key, interfaceSize }) => localStorage.setItem(key, JSON.stringify({ interfaceSize })), { key: PREFERENCES_KEY, interfaceSize })
        const settings = page.getByRole('button', { name: 'Chat configuration', exact: true })
        const input = page.getByRole('textbox', { name: 'Message', exact: true })
        const expectMatchingSize = async (name) => {
          const action = page.getByRole('button', { name, exact: true })
          await expect(action).toBeVisible()
          const settingsBox = await settings.boundingBox()
          const actionBox = await action.boundingBox()
          expect(actionBox.width).toBeCloseTo(settingsBox.width, 1)
          expect(actionBox.height).toBeCloseTo(settingsBox.height, 1)
          if (hasTouch) {
            expect(actionBox.width).toBeGreaterThanOrEqual(44)
            expect(actionBox.height).toBeGreaterThanOrEqual(44)
          }
        }

        // The welcome composer is only shown alongside the chat list on desktop.
        for (const path of width > 640 ? ['/chats', '/chats/alpha'] : ['/chats/alpha']) {
          await page.goto(path)
          await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeDisabled()
          await expectMatchingSize('Send message')
          await input.fill('Draft message')
          await settings.click()
          await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeEnabled()
          await expectMatchingSize('Send message')
        }

        state.data.active_turn = { id: 'running', steps: [], status: '' }
        await input.fill('')
        await page.reload()
        await expectMatchingSize('Stop response')
        await input.fill('Next message')
        await expectMatchingSize('Queue message')
      })
    }
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
      for (const name of ['Select agent', 'Select agent set', 'Reasoning effort', 'Select tools', 'Select projects', 'Send message']) {
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

test('model picker restores keyboard focus and clears its tooltip when focus moves away', async ({ page, context }) => {
  await mockDesign(context)
  await page.goto('/chats/alpha')
  const input = page.getByRole('textbox', { name: 'Message', exact: true })
  const model = page.getByRole('button', { name: 'Select model', exact: true })
  const tooltip = page.getByRole('tooltip').filter({ hasText: /^Model for this chat$/ })
  await input.focus()
  await page.keyboard.press('Tab') // Attach images
  await page.keyboard.press('Tab') // Select model, without hovering it
  await expect(model).toBeFocused()
  await expect(tooltip).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(page.getByPlaceholder('Search models')).toBeFocused()
  await expect(tooltip).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.locator('.q-menu')).toHaveCount(0)
  await expect(model).toBeFocused()
  await expect(tooltip).toBeVisible()
  await input.focus()
  await expect(page.getByRole('tooltip')).toHaveCount(0)
  await input.fill('Keep typing after closing the picker')
  await expect(input).toHaveValue('Keep typing after closing the picker')
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
  await page.getByLabel('New chat settings', { exact: true }).selectOption('defaults')
  await page.getByLabel('Density', { exact: true }).selectOption('compact')
  await page.getByRole('button', { name: 'Reset workspace', exact: true }).click()
  await expect(page.getByLabel('New chat settings', { exact: true })).toHaveValue('last')
  await expect(page.getByLabel('Density', { exact: true })).toHaveValue('comfortable')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'sand')
  expect(state.writes).toEqual([])
})

test('chat visibility settings are searchable, validated, persisted and reset without instance writes', async ({ page, context }) => {
  const state = await mockDesign(context)
  await page.goto('/settings/workspace')
  const keepSelected = page.getByRole('switch', { name: 'Keep selected chat visible', exact: true })
  const grace = page.getByRole('spinbutton', { name: 'Chat visibility grace period', exact: true })
  await expect(keepSelected).toBeChecked()
  await expect(grace).toHaveValue('60')
  await keepSelected.uncheck()
  await grace.fill('120')
  await grace.press('Tab')
  const saved = () => page.evaluate(key => JSON.parse(localStorage.getItem(key)), PREFERENCES_KEY)
  await expect.poll(async () => (await saved()).chatSelectionGraceSeconds).toBe(120)
  await expect.poll(async () => (await saved()).chatKeepSelectedVisible).toBe(false)
  for (const invalid of ['-1', '3601']) {
    await grace.fill(invalid)
    await grace.press('Tab')
    expect((await saved()).chatSelectionGraceSeconds).toBe(120)
  }
  await page.reload()
  await expect(keepSelected).not.toBeChecked()
  await expect(grace).toHaveValue('120')
  await grace.fill('0')
  await grace.press('Tab')
  await expect.poll(async () => (await saved()).chatSelectionGraceSeconds).toBe(0)
  await page.getByRole('button', { name: 'Reset workspace', exact: true }).click()
  await expect(keepSelected).toBeChecked()
  await expect(grace).toHaveValue('60')
  const search = page.getByRole('searchbox', { name: 'Search settings', exact: true })
  await search.fill('grace period')
  await page.getByRole('link', { name: /Chat visibility grace period/ }).click()
  await expect(page.locator('#chatSelectionGraceSeconds')).toBeFocused()
  await expect(grace).toBeInViewport()
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
