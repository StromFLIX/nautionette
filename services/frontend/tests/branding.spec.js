import { test, expect } from '@playwright/test'
import { brand } from '../src/brand.js'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockDesign } from './design-fixture.js'

test('new workspaces use the shared Denkzeug identity and a quiet, readable canvas', async ({ page, context }) => {
  await mockDesign(context)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/chats')
  await expect(page.getByRole('heading', { name: 'What should happen next?', exact: true })).toBeVisible()
  await expect(page).toHaveTitle(brand.name)
  await expect(page.locator('html')).toHaveAttribute('data-theme', brand.defaultTheme)
  await expect(page.locator('html')).toHaveCSS('color-scheme', 'light')
  await expect(page.locator('.welcome__wordmark')).toContainText(brand.name)
  await expect(page.getByRole('navigation', { name: 'Ways to work' })).toContainText('Create')
  await expect(page.getByRole('navigation', { name: 'Ways to work' })).toContainText('Process')
  await expect(page.getByRole('navigation', { name: 'Ways to work' })).toContainText('Show')
  const logo = await page.request.get(brand.logo)
  expect(logo.ok()).toBe(true)
  expect(await logo.text()).toContain('opacity="0.65"')
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('An idea worth keeping.')
  await expect.poll(() => page.locator('.composer__send').evaluate(element => getComputedStyle(element, '::before').backgroundColor)).toBe('rgb(32, 35, 44)')
  await expect(page.locator('.welcome')).toHaveCSS('font-family', /Sora/)
  expect(errors).toEqual([])
})

test('the rebrand preserves existing credentials, storage keys and deliberate design choices', async ({ page, context }) => {
  await context.addInitScript(key => {
    localStorage.setItem('nautionette.token', 'fixture-existing-access-token')
    localStorage.setItem(key, JSON.stringify({ theme: 'orbit', interfaceSize: 110, overrides: { orbit: { accent: '#008577', font: 'monospace' } } }))
  }, PREFERENCES_KEY)
  await mockDesign(context)
  await page.goto('/chats/alpha')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'orbit')
  await expect(page.locator('html')).toHaveCSS('--accent', '#008577')
  await expect(page.locator('body')).toHaveCSS('font-family', 'monospace')
  await expect(page.locator('.rail__brand')).toHaveAttribute('aria-label', brand.name)
  await expect(page.locator('.rail__brand img')).toHaveCSS('filter', 'invert(1)')
  expect(await page.evaluate(() => localStorage.getItem('nautionette.token'))).toBe('fixture-existing-access-token')
  expect(await page.evaluate(() => localStorage.getItem('denkzeug.token'))).toBeNull()
})

for (const width of [1440, 390, 320]) {
  test(`Denkzeug surfaces fit the workspace at ${width}px`, async ({ page, context }) => {
    await mockDesign(context)
    await page.setViewportSize({ width, height: 900 })
    for (const path of ['/chats/alpha', '/settings/general', '/settings/appearance', '/workflows/digest?tab=Run']) {
      await page.goto(path)
      await expect(page.locator('.shell')).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
      await expect(page).toHaveTitle(brand.name)
    }
  })
}
