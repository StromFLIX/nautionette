import { test, expect } from '@playwright/test'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockDesign } from './design-fixture.js'

for (const width of [320, 390, 901, 1440]) {
  test(`125% defaults enlarge text and controls without zoom or overflow at ${width}px`, async ({ page, context }) => {
    await mockDesign(context)
    await page.setViewportSize({ width, height: 800 })
    await page.goto('/chats/alpha')
    await expect(page.locator('html')).toHaveCSS('font-size', '20px')
    await expect(page.locator('html')).toHaveCSS('zoom', '1')
    await expect(page.locator('.bubble').first()).toHaveCSS('font-size', '18.75px')
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveCSS('font-size', '18.75px')
    const options = page.getByRole('button', { name: 'Chat options', exact: true })
    const box = await options.boundingBox()
    expect(box.height).toBeGreaterThanOrEqual(42)
    await expect(page.locator('.pane-head')).toBeInViewport({ ratio: 1 })
    await expect(page.locator('.thread__foot')).toBeInViewport({ ratio: 1 })
    await page.getByRole('button', { name: 'Select model', exact: true }).click()
    await expect(page.locator('.q-menu')).toBeInViewport({ ratio: 1 })
    await expect(page.getByPlaceholder('Search models')).toHaveCSS('font-size', '16.25px')
    await page.keyboard.press('Escape')
    await options.click()
    await page.getByRole('button', { name: /Rename$/ }).click()
    await expect(page.locator('.q-dialog .q-card')).toBeInViewport({ ratio: 1 })
    await page.keyboard.press('Escape')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}

test('interface size applies immediately, persists and syncs without resetting themes', async ({ page, context }) => {
  const state = await mockDesign(context)
  await page.goto('/settings/appearance')
  await page.getByRole('button', { name: 'Sand theme', exact: true }).click()
  const second = await context.newPage()
  await second.goto('/chats/alpha')
  await page.goto('/settings/workspace')
  const size = page.getByLabel('Interface size', { exact: true })
  await expect(size).toHaveValue('125')
  for (const value of [100, 110, 150]) {
    await size.selectOption(String(value))
    await expect(page.locator('html')).toHaveCSS('font-size', `${16 * value / 100}px`)
    await expect(second.locator('html')).toHaveCSS('font-size', `${16 * value / 100}px`)
    await expect(second.locator('.bubble').first()).toHaveCSS('font-size', `${15 * value / 100}px`)
  }
  await page.reload()
  await expect(size).toHaveValue('150')
  await expect(page.locator('html')).toHaveCSS('font-size', '24px')
  await page.getByRole('button', { name: 'Reset workspace', exact: true }).click()
  await expect(size).toHaveValue('125')
  await expect(second.locator('html')).toHaveCSS('font-size', '20px')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'sand')
  expect(state.writes).toEqual([])
  await second.close()
})

test.describe('phone sizing', () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 320, height: 568 } })

  test('size is adjustable on a phone and large controls survive the on-screen keyboard', async ({ page, context }) => {
    const state = await mockDesign(context)
    await page.goto('/settings/workspace')
    const size = page.getByLabel('Interface size', { exact: true })
    await size.selectOption('150')
    await page.reload()
    await expect(size).toHaveValue('150')
    await expect(page.locator('.settings__body')).toBeInViewport({ ratio: 1 })
    expect(await page.locator('.settings__body').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.goto('/chats/alpha')
    await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Readable with the keyboard open')
    await page.getByRole('button', { name: 'Chat configuration', exact: true }).tap()
    await page.setViewportSize({ width: 320, height: 320 })
    for (const name of ['Select agent', 'Select agent set', 'Reasoning effort', 'Select tools', 'Select projects', 'Send message']) {
      const control = page.getByRole('button', { name, exact: true })
      await control.scrollIntoViewIfNeeded()
      await expect(control).toBeInViewport({ ratio: 1 })
      expect((await control.boundingBox()).height).toBeGreaterThanOrEqual(44)
    }
    await page.getByRole('button', { name: 'Send message', exact: true }).tap()
    await expect.poll(() => state.sent.length).toBe(1)
    expect(state.sent[0].text).toBe('Readable with the keyboard open')
    await expect(page.locator('.pane-head')).toBeInViewport({ ratio: 1 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const viewport = await page.locator('meta[name="viewport"]').getAttribute('content')
    expect(viewport).not.toMatch(/user-scalable=no|maximum-scale/)
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).interfaceSize, PREFERENCES_KEY)).toBe(150)
  })
})
