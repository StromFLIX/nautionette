import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { definitions } from '../../website/definitions.mjs'
import { productTour } from '../../website/product-tour-data.mjs'

// Exercise the exact static site, with its production paths, without adding a
// second development server or changing the app's branding/router.
async function website (page, { appURL = '', environment = 'production' } = {}) {
  await page.route('https://api.github.com/**', route => route.fulfill({ status: 503, body: '' }))
  await page.context().route('http://denkzeug.test/**', async route => {
    const path = new URL(route.request().url()).pathname
    const config = { '/app-url.txt': appURL, '/environment.txt': environment, '/ios-url.txt': '' }
    if (path in config) return route.fulfill({ contentType: 'text/plain', body: config[path] })
    const file = new URL(`../../website${path === '/' ? '/index.html' : path}`, import.meta.url)
    const mime = { html: 'text/html', css: 'text/css', js: 'text/javascript', mjs: 'text/javascript', svg: 'image/svg+xml', webp: 'image/webp', woff2: 'font/woff2' }
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

test('evolving patterns keep the title and definition clear on desktop and phones', async ({ page }) => {
  await website(page)
  await page.clock.install()
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    for (const [locale, advance] of [['en', 100], ['de', 3000], ['ar', 3500]]) {
      await page.getByLabel('Definition language').selectOption(locale)
      await page.clock.runFor(advance)
      const alpha = await page.locator('canvas').evaluate(canvas => {
        const bounds = canvas.getBoundingClientRect()
        const ratio = canvas.width / bounds.width
        const context = canvas.getContext('2d')
        return ['.hero h1', '.definition'].map(selector => {
          const box = document.querySelector(selector).getBoundingClientRect()
          const pixels = context.getImageData(
            Math.ceil((box.left - bounds.left + 8) * ratio), Math.ceil((box.top - bounds.top + 8) * ratio),
            Math.floor((box.width - 16) * ratio), Math.floor((box.height - 16) * ratio)
          ).data
          let maximum = 0
          for (let i = 3; i < pixels.length; i += 4) maximum = Math.max(maximum, pixels[i])
          return maximum
        })
      })
      expect(alpha).toEqual([0, 0])
    }
  }
})

for (const width of [1440, 768, 390, 320]) {
  test(`product screenshots keep desktop-to-phone order and fit at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await website(page)
    const product = page.getByRole('region', { name: 'A workspace for your AI agents.' })
    await product.scrollIntoViewIfNeeded()
    await expect(product.getByRole('navigation', { name: 'Demo screenshots' })).toBeVisible()
    await expect(product).toContainText('open-source app you run on your own server')
    await expect(product).toContainText('demo data')
    for (const image of await product.locator('img').all()) {
      await image.scrollIntoViewIfNeeded()
      await expect.poll(() => image.evaluate(node => node.complete && node.naturalWidth > 0)).toBe(true)
    }
    const desktop = await product.locator('.product-view--desktop').boundingBox()
    const phone = await product.locator('.product-view--phone').boundingBox()
    if (width <= 700) expect(desktop.y + desktop.height).toBeLessThanOrEqual(phone.y)
    else expect(desktop.x + desktop.width).toBeLessThanOrEqual(phone.x)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    const link = product.getByRole('link', { name: /full-size desktop workspace/ })
    const originalPath = await link.getAttribute('href')
    expect(originalPath).toMatch(/^\/assets\/(product|product-tour)\/[a-z-]+\.webp$/)
    await link.focus()
    await expect(link).toBeFocused()
    const opened = page.waitForEvent('popup')
    await link.press('Enter')
    const original = await opened
    await expect(original).toHaveURL(new URL(originalPath, 'http://denkzeug.test').href)
    await expect.poll(() => original.locator('img').evaluate(image => image.complete && image.naturalWidth === 1920)).toBe(true)
    await original.close()
  })
}

for (const width of [1440, 390, 320]) {
  test(`actual screenshot targets switch images without recreating the app at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const requests = []; const errors = []
    page.on('request', request => requests.push({ url: request.url(), method: request.method() }))
    page.on('pageerror', error => errors.push(error.message))
    await website(page)
    const tour = page.locator('[data-product-tour]')
    const navigation = tour.getByRole('navigation', { name: 'Demo screenshots' })
    await expect(navigation).toBeVisible()
    const image = tour.locator('[data-tour-image]')
    await expect(image).toHaveAttribute('src', productTour.scenes.find(scene => scene.id === 'chat').src)
    expect(new Set(requests.filter(request => request.url.includes('/assets/product-tour/')).map(request => request.url)).size).toBe(1)
    await expect(tour.locator('input, textarea, select, canvas')).toHaveCount(0)

    if (width === 1440) {
      const hotspot = productTour.scenes.find(scene => scene.id === 'chat').hotspots.find(hotspot => hotspot.target === 'workflows')
      const target = tour.getByRole('group', { name: 'Screenshot click targets' }).getByRole('button', { name: hotspot.label, exact: true })
      await target.scrollIntoViewIfNeeded()
      const box = await target.boundingBox(); const capture = await image.boundingBox()
      expect(Math.abs((box.x - capture.x) / capture.width - hotspot.rect.x)).toBeLessThan(.003)
      expect(Math.abs((box.y - capture.y) / capture.height - hotspot.rect.y)).toBeLessThan(.003)
      await target.click()
      await expect(image).toHaveAttribute('src', productTour.scenes.find(scene => scene.id === 'workflows').src)
    } else await expect(tour.getByRole('group', { name: 'Screenshot click targets' })).toBeHidden()

    for (const scene of productTour.scenes) {
      await navigation.getByRole('button', { name: scene.title, exact: true }).click()
      await expect(image).toHaveAttribute('src', scene.src)
      await expect(image).toHaveAttribute('alt', scene.alt)
      await expect(navigation.getByRole('button', { name: scene.title, exact: true })).toHaveAttribute('aria-pressed', 'true')
      await expect.poll(() => image.evaluate(node => node.complete && node.naturalWidth === 1920)).toBe(true)
    }
    await tour.getByRole('button', { name: 'Back to chat', exact: true }).click()
    await expect(image).toHaveAttribute('src', productTour.scenes.find(scene => scene.id === 'chat').src)
    await expect(tour.getByRole('button', { name: 'Previous screen', exact: true })).toBeDisabled()
    const desktop = await page.locator('.product-view--desktop').boundingBox()
    const phone = await page.locator('.product-view--phone').boundingBox()
    if (width <= 700) expect(desktop.y + desktop.height).toBeLessThanOrEqual(phone.y)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    expect(requests.filter(request => request.method !== 'GET' || /\/api\//.test(request.url))).toEqual([])
    expect(errors).toEqual([])
  })
}

test('a missing tour screenshot leaves the last real image and other page features intact', async ({ page }) => {
  await website(page)
  const tour = page.locator('[data-product-tour]')
  const navigation = tour.getByRole('navigation', { name: 'Demo screenshots' })
  await expect(navigation).toBeVisible()
  const scene = productTour.scenes.find(scene => scene.id === 'settings')
  await page.route(`**${scene.src}`, route => route.fulfill({ status: 404, body: '' }))
  await navigation.getByRole('button', { name: scene.title, exact: true }).click()
  await expect(tour.getByRole('status')).toContainText('previous screenshot is still available')
  await expect(tour.locator('[data-tour-image]')).toHaveAttribute('src', productTour.scenes.find(scene => scene.id === 'chat').src)
  await page.getByLabel('Definition language').selectOption('de')
  await expect(page.locator('#definition')).toHaveText(definitions.de)
})

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
  await expect.poll(pixels, { timeout: 3500 }).not.toBe(still)
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
  await expect.poll(pixels, { timeout: 3500 }).not.toBe(offscreen)
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
  await expect(page.getByRole('navigation', { name: 'Demo screenshots' })).toHaveCount(0)
  await expect(page.locator('[data-tour-image]')).toBeVisible()
  await context.close()
})
