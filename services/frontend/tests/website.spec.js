import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { definitions } from '../../website/definitions.mjs'

// Exercise the exact static site, with its production paths, without adding a
// second development server or changing the app's branding/router.
async function website (page, { appURL = '', environment = 'production' } = {}) {
  await page.route('https://api.github.com/**', route => route.fulfill({ status: 503, body: '' }))
  await page.route('http://denkzeug.test/**', async route => {
    const path = new URL(route.request().url()).pathname
    const config = { '/app-url.txt': appURL, '/environment.txt': environment, '/ios-url.txt': '' }
    if (path in config) return route.fulfill({ contentType: 'text/plain', body: config[path] })
    const file = new URL(`../../website${path === '/' ? '/index.html' : path}`, import.meta.url)
    const mime = { html: 'text/html', css: 'text/css', js: 'text/javascript', mjs: 'text/javascript', svg: 'image/svg+xml', woff2: 'font/woff2' }
    try {
      await route.fulfill({ contentType: mime[file.pathname.split('.').at(-1)] || 'text/plain', body: await readFile(file) })
    } catch { await route.fulfill({ status: 404, body: 'Not found' }) }
  })
  await page.goto('http://denkzeug.test/')
  await page.evaluate(() => document.fonts.ready)
}

for (const width of [1440, 390, 320]) {
  test(`Denkzeug website is readable and usable at ${width}px`, async ({ page }) => {
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({ width, height: 900 })
    await website(page)
    await expect(page.getByRole('heading', { name: 'Denkzeug', exact: true })).toBeVisible()
    await expect(page.locator('#definition')).toHaveText(definitions.en)
    const language = page.getByLabel('Definition language')
    for (const [locale, text] of Object.entries(definitions)) {
      await language.selectOption(locale)
      await expect(page.locator('#definition')).toHaveText(text)
      await expect(page.locator('#definition')).toHaveAttribute('lang', locale)
      await expect(page.locator('#definition')).toHaveAttribute('dir', locale === 'ar' ? 'rtl' : 'ltr')
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
      // Longer definitions may grow the hero, but must never collide with its controls.
      const actions = await page.locator('.hero-actions').boundingBox()
      const caption = await page.locator('.field-caption').boundingBox()
      expect(actions.y + actions.height).toBeLessThan(caption.y)
    }
    await language.selectOption('de')
    await page.reload()
    await expect(language).toHaveValue('de')
    await language.selectOption('en')
    for (const name of ['Free', 'Remote', 'Extensible', 'Create', 'Process', 'Show']) {
      await expect(page.getByRole('heading', { name, exact: true })).toHaveCount(1)
    }
    await page.getByRole('link', { name: 'Make it yours' }).click()
    await expect(page.getByRole('heading', { name: 'A little more room to think.' })).toBeInViewport()
    await page.getByText('Already hosting? Get a client.').click()
    await expect(page.locator('#release-status')).toContainText('Check GitHub Releases')
    await expect(page.locator('#release-downloads a')).toHaveCount(0)
    expect(errors).toEqual([])
  })
}

test('motion pauses, persists and defaults to a still field for reduced motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await website(page)
  const button = page.getByRole('button', { name: 'Play activity' })
  await expect(button).toHaveAttribute('aria-pressed', 'true')
  const pixels = () => page.locator('canvas').evaluate(canvas => canvas.toDataURL())
  const still = await pixels()
  await page.waitForTimeout(650)
  expect(await pixels()).toBe(still)
  await button.click() // A visitor can explicitly opt in, even with reduced motion.
  await expect(page.getByRole('button', { name: 'Pause activity' })).toBeVisible()
  await page.waitForTimeout(650)
  expect(await pixels()).not.toBe(still)
  await page.getByRole('button', { name: 'Pause activity' }).click()
  const paused = await pixels()
  await page.waitForTimeout(650)
  expect(await pixels()).toBe(paused)
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.reload()
  await expect(button).toHaveAttribute('aria-pressed', 'true')
})

test('the cellular field stops rendering when the hero is off-screen', async ({ page }) => {
  await website(page)
  const pixels = () => page.locator('canvas').evaluate(canvas => canvas.toDataURL())
  await page.locator('#start').scrollIntoViewIfNeeded()
  await page.waitForTimeout(150)
  const offscreen = await pixels()
  await page.waitForTimeout(700)
  expect(await pixels()).toBe(offscreen)
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
  await page.waitForTimeout(700)
  expect(await pixels()).not.toBe(offscreen)
})

test('blocked storage, clipboard denial and unsafe instance URLs have usable fallbacks', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', { get () { throw new Error('Storage blocked') } })
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => { throw new Error('Denied') } } })
  })
  await website(page, { appURL: 'javascript:alert(1)' })
  await expect(page.getByRole('button', { name: 'Pause activity' })).toBeVisible()
  await page.getByLabel('Definition language').selectOption('ja')
  await expect(page.locator('#definition')).toHaveText(definitions.ja)
  await expect(page.locator('#app-link')).toHaveAttribute('href', 'https://github.com/StromFLIX/nautionette')
  await expect(page.locator('.setup-card code')).toHaveText('git clone https://github.com/StromFLIX/nautionette')
  await page.getByRole('button', { name: 'Copy command' }).click()
  await expect(page.getByRole('status', { name: '' }).filter({ hasText: 'Select the command below' })).toBeVisible()
})

test('keyboard navigation, runtime app URLs and staging indicators remain intact', async ({ page }) => {
  await website(page, { appURL: 'https://my-instance.example/', environment: 'staging' })
  await expect(page.locator('#app-link')).toHaveAttribute('href', 'https://my-instance.example/')
  await expect(page.locator('#app-link')).toHaveText('Open your instance')
  await expect(page.locator('#staging-banner')).toBeVisible()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('main')).toBeInViewport()
})

test('without JavaScript, the brand, definition, setup and releases are still available', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false })
  const page = await context.newPage()
  await website(page)
  await expect(page.getByRole('heading', { name: 'Denkzeug', exact: true })).toBeVisible()
  await expect(page.locator('#definition')).toHaveText(definitions.en)
  await expect(page.getByRole('link', { name: 'Self-host Denkzeug' })).toHaveAttribute('href', /#run-it$/)
  await page.getByText('Already hosting? Get a client.').click()
  await expect(page.getByRole('link', { name: 'All releases and downloads' })).toBeVisible()
  await expect(page.locator('#motion-toggle')).toBeHidden()
  await expect(page.getByLabel('Definition language')).toBeHidden()
  await expect(page.getByRole('button', { name: 'Copy command' })).toBeHidden()
  await context.close()
})
