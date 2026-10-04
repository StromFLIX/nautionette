import { test, expect } from '@playwright/test'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockDesign } from './design-fixture.js'

const longTitle = 'Investigate spinners and timing attribution without wasting space in the conversation list'

async function fixture (context, preferences = {}) {
  const state = await mockDesign(context)
  state.data.chat.title = longTitle
  await context.addInitScript(({ key, preferences }) => {
    localStorage.setItem(key, JSON.stringify({ sideWidth: 260, ...preferences }))
  }, { key: PREFERENCES_KEY, preferences })
  return state
}

const chatRow = page => page.locator('.chat-list-item')
const options = page => page.getByRole('button', { name: `Options for ${longTitle}`, exact: true })

async function expectFullWidth (row) {
  const wrapper = await row.boundingBox()
  const link = await row.locator('a').boundingBox()
  expect(link.x).toBeCloseTo(wrapper.x)
  expect(link.width).toBeCloseTo(wrapper.width)
}

async function expectActionFits (row, menu) {
  const box = await row.boundingBox()
  const action = await menu.boundingBox()
  const time = await row.locator('.row-item__time').boundingBox()
  const title = await row.locator('.row-item__title').boundingBox()
  expect(title.width).toBeGreaterThan(0)
  expect(title.x + title.width).toBeLessThan(time.x)
  expect(time.x + time.width).toBeLessThanOrEqual(action.x)
  expect(action.x + action.width).toBeLessThanOrEqual(box.x + box.width)
  expect(action.y).toBeGreaterThanOrEqual(box.y)
  expect(action.y + action.height).toBeLessThanOrEqual(box.y + box.height)
}

for (const theme of ['orbit', 'daylight']) {
  for (const interfaceSize of [100, 150]) {
    test(`chat actions only use title space during hover in ${theme} at ${interfaceSize}%`, async ({ page, context }) => {
      await fixture(context, { theme, interfaceSize })
      await page.goto('/chats/alpha')
      const row = chatRow(page)
      const menu = options(page)
      const title = row.locator('.row-item__title')
      await expect(row.locator('a')).toHaveClass(/row-item--active/)
      await expectFullWidth(row)
      await expect(menu).toHaveCSS('opacity', '0')
      await expect(menu).toHaveCSS('pointer-events', 'none')
      await expect(title).toHaveCSS('font-weight', '400')
      await expect(title).toHaveCSS('text-overflow', 'ellipsis')
      const before = await title.boundingBox()
      const height = (await row.boundingBox()).height
      const fontSize = await title.evaluate(el => getComputedStyle(el).fontSize)
      const time = await row.locator('.row-item__time').boundingBox()
      const link = await row.locator('a').boundingBox()
      expect(link.x + link.width - time.x - time.width).toBeCloseTo(10)
      if (interfaceSize === 150) await page.screenshot({ path: `/tmp/nautionette-chat-row-${theme}-idle.png` })

      await row.hover()
      await expect(menu).toHaveCSS('opacity', '1')
      await expect(menu).toHaveCSS('pointer-events', 'auto')
      await expectActionFits(row, menu)
      await expectFullWidth(row)
      expect((await title.boundingBox()).width).toBeLessThan(before.width)
      expect((await row.boundingBox()).height).toBeCloseTo(height)
      await expect(title).toHaveCSS('font-size', fontSize)
      await expect(title).toHaveCSS('font-weight', '400')
      if (interfaceSize === 150) await page.screenshot({ path: `/tmp/nautionette-chat-row-${theme}-hover.png` })

      await page.mouse.move(0, 0)
      await expect(menu).toHaveCSS('opacity', '0')
      expect((await title.boundingBox()).width).toBeCloseTo(before.width)
      expect(await page.locator('.side__list').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    })
  }
}

test('keyboard focus reveals options and an open popover keeps its trigger visible', async ({ page, context }) => {
  await fixture(context)
  await page.goto('/chats')
  const row = chatRow(page)
  const menu = options(page)
  await row.locator('a').focus()
  await expect(menu).toHaveCSS('opacity', '1')
  await expectActionFits(row, menu)
  await page.keyboard.press('Tab')
  await expect(menu).toBeFocused()
  await expect(menu).toHaveAttribute('aria-expanded', 'false')
  await page.keyboard.press('Enter')
  const markUnread = page.getByRole('button', { name: 'Mark as unread', exact: true })
  await expect(markUnread).toBeVisible()
  await markUnread.focus()
  await page.mouse.move(0, 0)
  await expect(menu).toHaveAttribute('aria-expanded', 'true')
  await expect(menu).toHaveCSS('opacity', '1')
  await expectActionFits(row, menu)
  await expect(page).toHaveURL(/\/chats$/)
  await page.keyboard.press('Escape')
  await expect(markUnread).toBeHidden()
  await expect(menu).toBeFocused()
  await expect(menu).toHaveAttribute('aria-expanded', 'false')
  await page.getByRole('textbox', { name: 'Search chats', exact: true }).focus()
  await expect(menu).toHaveCSS('opacity', '0')
})

test('unread uses gentle emphasis and retains its dot, while running titles stay regular', async ({ page, context }) => {
  const state = await fixture(context)
  state.data.chat.unread = true
  await page.goto('/chats')
  const row = chatRow(page)
  await expect(row.locator('.row-item__title')).toHaveCSS('font-weight', '500')
  await expect(row.getByLabel('Unread messages', { exact: true })).toBeVisible()
  state.data.chat.unread = false
  state.data.chat.answering = true
  await page.evaluate(async () => (await import('/src/store.js')).actions.loadChats())
  await expect(row.locator('a')).toHaveClass(/row-item--running/)
  await expect(row.locator('.row-item__title')).toHaveCSS('font-weight', '400')
  await expect(row.getByRole('img', { name: 'Active', exact: true })).toBeVisible()
})

test.describe('touch actions', () => {
  test.use({ hasTouch: true })
  for (const width of [390, 1440]) {
    test(`options remain visible and tappable without hover at ${width}px`, async ({ page, context }) => {
      await fixture(context, { interfaceSize: 150 })
      await page.setViewportSize({ width, height: 900 })
      await page.goto('/chats')
      const row = chatRow(page)
      const menu = options(page)
      await expect(menu).toHaveCSS('opacity', '1')
      const box = await menu.boundingBox()
      expect(box.width).toBeGreaterThanOrEqual(44)
      expect(box.height).toBeGreaterThanOrEqual(44)
      await expectFullWidth(row)
      await expectActionFits(row, menu)
      await page.screenshot({ path: `/tmp/nautionette-chat-row-touch-${width}.png` })
      await menu.tap()
      await expect(page.getByRole('button', { name: 'Mark as unread', exact: true })).toBeVisible()
      await expect(page).toHaveURL(/\/chats$/)
      expect(await page.locator('.side__list').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    })
  }
})
