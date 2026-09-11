import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockDesign } from './design-fixture.js'

const saved = page => page.evaluate(key => JSON.parse(localStorage.getItem(key)), PREFERENCES_KEY)
const pack = (patch = {}) => ({ format: 'nautionette-skin', version: 2, id: 'custom', name: 'My custom design', base: 'sand',
  tokens: { accent: '#7a315d', 'radius-sm': 0 }, css: '.bubble { border: 3px dashed #7a315d; }', assets: {}, ...patch })
const upload = (page, data) => page.locator('#theme-transfer input[type=file]').setInputFiles({ name: 'custom.skin.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data)) })

for (const [id, name] of [['winamp-classic', 'Winamp Classic'], ['windows-xp', 'Windows XP']]) {
  for (const width of [1440, 320]) {
    test(`${name} is a usable complete design at ${width}px, persists and cleanly switches off`, async ({ page, context }) => {
      const state = await mockDesign(context)
      const errors = []
      page.on('pageerror', error => errors.push(error.message))
      await page.setViewportSize({ width, height: width === 320 ? 568 : 1000 })
      await page.goto('/settings/appearance')
      await page.getByRole('button', { name: `Use ${name} skin`, exact: true }).click()
      await expect(page.locator('html')).toHaveAttribute('data-skin', id)
      await expect(page.getByRole('button', { name: `Use ${name} skin`, exact: true })).toHaveAttribute('aria-pressed', 'true')
      await expect.poll(async () => (await saved(page)).skin).toBe(id)
      await page.reload()
      await expect(page.locator('html')).toHaveAttribute('data-skin', id)
      await expect(page.locator('iframe').first()).toHaveAttribute('sandbox', '')
      await page.goto('/chats/alpha')
      await expect(page.locator('[data-skin-part=header]')).toHaveCSS('background-image', /linear-gradient/)
      await expect(page.locator('[data-skin-part=composer]')).toHaveCSS('border-radius', id === 'windows-xp' ? '3px' : '2.5px')
      const input = page.getByRole('textbox', { name: 'Message', exact: true })
      await expect(input).toBeInViewport()
      await input.fill('This still works')
      await page.getByRole('button', { name: 'Chat configuration', exact: true }).click()
      await expect(page.getByRole('button', { name: 'Select model', exact: true })).toBeInViewport()
      await page.getByRole('button', { name: 'Chat configuration', exact: true }).click()
      await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeInViewport()
      if (id === 'windows-xp' && width > 900) {
        const nav = await page.locator('[data-skin-part=navigation]').boundingBox()
        const main = await page.locator('[data-skin-part=main]').boundingBox()
        expect(nav.y).toBeGreaterThanOrEqual(main.y + main.height)
        expect(nav.width).toBeGreaterThan(1300)
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await page.screenshot({ path: `/tmp/nautionette-skin-${id}-${width}.png` })
      await page.getByRole('button', { name: 'Send message', exact: true }).click()
      await expect.poll(() => state.sent.length).toBe(1)
      await page.goto('/workflows/digest?tab=Code')
      await expect(page.locator('[data-skin-part=code]')).toBeVisible()
      await page.goto('/settings/appearance')
      await page.getByRole('button', { name: 'Orbit theme', exact: true }).click()
      await expect(page.locator('html')).toHaveAttribute('data-skin', '')
      await expect(page.locator('#nautionette-skin')).toHaveCount(0)
      await page.goto('/chats/alpha')
      await expect(page.locator('.pane-head')).toHaveCSS('background-image', 'none')
      expect(state.writes).toEqual([])
      expect(errors).toEqual([])
    })
  }
}

test.describe('skin touch layouts', () => {
  test.use({ hasTouch: true, isMobile: true })
  for (const name of ['Winamp Classic', 'Windows XP']) {
    for (const interfaceSize of [100, 150]) {
      test(`${name} keeps expanded controls reachable at ${interfaceSize}% with the keyboard open`, async ({ page, context }) => {
        await mockDesign(context)
        await page.setViewportSize({ width: 320, height: 568 })
        await page.goto('/settings/appearance')
        await page.getByRole('button', { name: `Use ${name} skin`, exact: true }).tap()
        await page.goto('/settings/workspace')
        await page.getByLabel('Interface size', { exact: true }).selectOption(String(interfaceSize))
        await page.goto('/chats/alpha')
        await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Draft\nwith\nseveral\nlines')
        await page.getByRole('button', { name: 'Chat configuration', exact: true }).tap()
        await page.setViewportSize({ width: 320, height: 320 })
        for (const label of ['Select agent', 'Select model', 'Select tools', 'Select projects', 'Send message']) {
          const control = page.getByRole('button', { name: label, exact: true })
          await control.scrollIntoViewIfNeeded()
          await expect(control).toBeInViewport({ ratio: 1 })
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      })
    }
  }
})

test('custom packs import atomically, isolate tweaks, export, reset, replace and remove', async ({ page, context }) => {
  await mockDesign(context)
  await page.goto('/settings/appearance')
  await upload(page, pack())
  await expect(page.getByText('My custom design skin imported.', { exact: true })).toBeVisible()
  await expect(page.locator('html')).toHaveCSS('--accent', '#7a315d')
  const before = await saved(page)
  await upload(page, pack({ css: '.a{background:image-set("https://tracker.test/secret" 1x)}' }))
  await expect(page.getByRole('alert')).toContainText('Unsupported CSS function')
  expect(await saved(page)).toEqual(before)
  await page.goto('/settings/appearance#token-accent')
  const accent = page.getByLabel('Accent', { exact: true })
  await accent.fill('#234567')
  await accent.press('Tab')
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export skin pack', exact: true }).click()
  const download = await downloaded
  const exported = JSON.parse(await readFile(await download.path(), 'utf8'))
  expect(exported.tokens.accent).toBe('#234567')
  expect(exported.css).toBe(pack().css)
  expect(exported.theme).toBeUndefined()
  expect(exported.skins).toBeUndefined()
  await page.getByRole('button', { name: 'Reset theme', exact: true }).click()
  await expect(page.locator('html')).toHaveCSS('--accent', '#7a315d')
  await page.getByRole('button', { name: 'Undo reset', exact: true }).click()
  await expect(page.locator('html')).toHaveCSS('--accent', '#234567')
  await page.getByRole('button', { name: 'Sand theme', exact: true }).click()
  await expect(page.locator('html')).toHaveCSS('--accent', '#9d492e')
  await page.getByRole('button', { name: 'Use My custom design skin', exact: true }).click()
  await expect(page.locator('html')).toHaveCSS('--accent', '#234567')
  await upload(page, pack({ name: 'Updated design', tokens: { accent: '#654321' } }))
  await expect(page.locator('html')).toHaveCSS('--accent', '#654321')
  expect((await saved(page)).skins).toHaveLength(1)
  await page.getByRole('button', { name: 'Remove Updated design skin', exact: true }).click()
  await expect(page.locator('#nautionette-skin')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Use Updated design skin', exact: true })).toHaveCount(0)
  expect((await saved(page)).skins).toEqual([])
  expect((await saved(page)).overrides['skin:custom']).toBeUndefined()
})

test('example downloads are standalone packs, and legacy imports deactivate custom CSS', async ({ page, context }) => {
  await mockDesign(context)
  await page.goto('/settings/appearance')
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download Windows XP skin', exact: true }).click()
  const download = await downloaded
  const text = await readFile(await download.path(), 'utf8')
  expect(JSON.parse(text).assets.hills).toMatch(/^data:image\/png;base64,/)
  await upload(page, JSON.parse(text))
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'windows-xp')
  await upload(page, { version: 1, theme: 'nebula', overrides: {} })
  await expect(page.locator('#nautionette-skin')).toHaveCount(0)
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'nebula')
  expect((await saved(page)).skins).toHaveLength(1)
})

test('bundled assets and sandboxed previews make no external requests', async ({ page, context }) => {
  await mockDesign(context)
  const external = []
  page.on('request', request => {
    if (/^https?:/.test(request.url()) && new URL(request.url()).hostname !== '127.0.0.1') external.push(request.url())
  })
  await page.goto('/settings/appearance')
  for (const name of ['Winamp Classic', 'Windows XP']) {
    await page.getByRole('button', { name: `Use ${name} skin`, exact: true }).click()
    const preview = page.locator('.skin-card').filter({ has: page.getByRole('button', { name: `Use ${name} skin`, exact: true }) }).locator('iframe')
    await preview.scrollIntoViewIfNeeded()
    await expect(preview.contentFrame().locator('[data-skin-part=header]')).toHaveCSS('background-image', /linear-gradient/)
    await expect(preview.contentFrame().locator('meta[http-equiv="Content-Security-Policy"]')).toHaveAttribute('content', /default-src 'none'/)
  }
  await upload(page, pack({ css: '.shell{background:url("https://tracker.test/private")}' }))
  await expect(page.getByRole('alert')).toContainText('bundled asset')
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'windows-xp')
  expect(external).toEqual([])
})

test('corrupt persisted skins cannot break startup or restore unsafe CSS', async ({ page, context }) => {
  await mockDesign(context)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/settings/appearance')
  await page.evaluate(({ key, skin }) => localStorage.setItem(key, JSON.stringify({ theme: 'sand', skin: skin.id, skins: [skin] })),
    { key: PREFERENCES_KEY, skin: pack({ css: '@import "https://tracker.test/private";' }) })
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-skin', '')
  await expect(page.locator('#nautionette-skin')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Sand theme', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'Use My custom design skin', exact: true })).toHaveCount(0)
  expect(errors).toEqual([])
})

test('skin library and active selection sync between tabs, including removal', async ({ page, context }) => {
  await mockDesign(context)
  await page.goto('/settings/appearance')
  const other = await context.newPage()
  await other.goto('/settings/appearance')
  await upload(page, pack())
  await expect(other.locator('html')).toHaveAttribute('data-skin', 'custom')
  await expect(other.getByRole('button', { name: 'Use My custom design skin', exact: true })).toBeVisible()
  await other.getByRole('button', { name: 'Remove My custom design skin', exact: true }).click()
  await expect(page.locator('#nautionette-skin')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Use My custom design skin', exact: true })).toHaveCount(0)
})

test('keyboard and startup safe mode recover even if a pack hides the whole app', async ({ page, context }) => {
  await mockDesign(context)
  await page.goto('/settings/appearance')
  await upload(page, pack({ css: '#app{display:none!important}' }))
  await expect(page.locator('#app')).toBeHidden()
  await page.keyboard.press('Control+Alt+0')
  await expect(page.locator('#app')).toBeVisible()
  await expect(page.locator('#nautionette-skin')).toHaveCount(0)
  expect((await saved(page)).skins).toHaveLength(1)
  await page.getByRole('button', { name: 'Use My custom design skin', exact: true }).click()
  await expect(page.locator('#app')).toBeHidden()
  await page.goto('/settings/appearance?safe-appearance=1')
  await expect(page.locator('#app')).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'orbit')
  await expect(page.locator('html')).toHaveAttribute('data-skin', '')
  await page.goto('/settings/appearance')
  await expect(page.locator('html')).toHaveAttribute('data-skin', '')
  expect((await saved(page)).skins).toHaveLength(1)
})

test('pack motion follows OS changes and workspace reduced motion', async ({ page, context }) => {
  await mockDesign(context)
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.goto('/settings/appearance')
  await upload(page, pack({ css: '@keyframes custom-pulse{to{opacity:.9}} .shell{animation:custom-pulse 10s infinite}' }))
  await expect(page.locator('.shell')).toHaveCSS('animation-name', 'custom-pulse')
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await expect(page.locator('.shell')).toHaveCSS('animation-name', 'none')
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await expect(page.locator('.shell')).toHaveCSS('animation-name', 'custom-pulse')
  await page.goto('/settings/workspace')
  await page.getByLabel('Motion', { exact: true }).selectOption('reduced')
  await expect(page.locator('.shell')).toHaveCSS('animation-name', 'none')
})

test('storage failure is reported without preventing session styling or recovery', async ({ page, context }) => {
  await mockDesign(context)
  await page.goto('/settings/appearance')
  await page.evaluate(() => { Storage.prototype.setItem = () => { throw new DOMException('Full', 'QuotaExceededError') } })
  await upload(page, pack())
  await expect(page.locator('html')).toHaveAttribute('data-skin', 'custom')
  await expect(page.getByRole('alert')).toContainText('session only')
  await page.keyboard.press('Control+Alt+0')
  await expect(page.locator('html')).toHaveAttribute('data-skin', '')
  await expect(page.locator('#nautionette-skin')).toHaveCount(0)
})
