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
  const status = await row.locator('.chat-status').boundingBox()
  const title = await row.locator('.row-item__title').boundingBox()
  expect(title.width).toBeGreaterThan(0)
  expect(title.x + title.width).toBeLessThan(status.x)
  expect(status.x + status.width).toBeLessThan(action.x)
  await expect(row.locator('.row-item__time')).toBeHidden()
  expect(action.x + action.width).toBeLessThanOrEqual(box.x + box.width)
  expect(action.y).toBeGreaterThanOrEqual(box.y)
  expect(action.y + action.height).toBeLessThanOrEqual(box.y + box.height)
}

for (const theme of ['orbit', 'daylight']) {
  for (const interfaceSize of [100, 150]) {
    test(`chat time and options share a stable slot in ${theme} at ${interfaceSize}%`, async ({ page, context }) => {
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
      await expect(row.locator('.row-item__time')).toBeVisible()
      const slot = await row.locator('.chat-list-item__trailing').boundingBox()
      const status = await row.locator('.chat-status').boundingBox()
      const link = await row.locator('a').boundingBox()
      expect(slot.x + slot.width).toBeLessThanOrEqual(link.x + link.width)
      expect(before.x + before.width).toBeLessThan(status.x)
      if (interfaceSize === 150) await page.screenshot({ path: `/tmp/nautionette-chat-row-${theme}-idle.png` })

      await row.hover()
      await expect(menu).toHaveCSS('opacity', '1')
      await expect(menu).toHaveCSS('pointer-events', 'auto')
      await expectActionFits(row, menu)
      await expectFullWidth(row)
      expect((await title.boundingBox()).width).toBeCloseTo(before.width)
      expect((await row.locator('.chat-status').boundingBox()).x).toBeCloseTo(status.x)
      expect((await row.locator('.chat-list-item__trailing').boundingBox()).x).toBeCloseTo(slot.x)
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

test('standalone filled squares pulse for activity and unread uses independent title emphasis', async ({ page, context }) => {
  const state = await fixture(context)
  state.data.chat.unread = true
  await page.goto('/chats')
  const row = chatRow(page)
  await expect(row.locator('.row-item__title')).toHaveCSS('font-weight', '500')
  await expect(row.getByRole('img', { name: 'Inactive · Unread messages', exact: true })).toBeVisible()
  await expect(row.locator('a')).toHaveAccessibleDescription('Inactive · Unread messages')
  await expect(row.locator('.row-item__unread, circle, .chat-status__lines, .chat-list-item__tree, .chat-list-item__count')).toHaveCount(0)
  await expect(row.locator('.chat-status rect')).toHaveAttribute('width', '18')
  await expect(row.locator('.chat-status')).not.toHaveCSS('fill', 'none')
  state.data.chat.unread = false
  state.data.chat.answering = true
  await page.evaluate(async () => (await import('/src/store.js')).actions.loadChats())
  await expect(row.locator('a')).toHaveClass(/row-item--running/)
  await expect(row.locator('.row-item__title')).toHaveCSS('font-weight', '400')
  const status = row.getByRole('img', { name: 'Active', exact: true })
  await expect(status).toBeVisible()
  await expect(status).toHaveCSS('animation-name', 'chat-status-pulse')
  await expect(status).toHaveCSS('animation-duration', '2.8s')
  state.data.chat.unread = true
  await page.evaluate(async () => (await import('/src/store.js')).actions.loadChats())
  await expect(row.locator('a')).toHaveAccessibleDescription('Active · Unread messages')
  await expect(row.locator('.row-item__title')).toHaveCSS('font-weight', '500')
  await expect(row.locator('.chat-status')).toHaveCSS('animation-name', 'chat-status-pulse')
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await expect(row.locator('.chat-status')).toHaveCSS('animation-name', 'none')
  await expect(row.locator('.chat-status')).not.toHaveCSS('fill', 'none')
})

async function alignmentFixture (page, context, preferences = {}) {
  await fixture(context, preferences)
  await page.clock.setFixedTime(new Date('2026-11-11T15:00:00Z'))
  const times = ['2026-11-11T13:11:00Z', '2026-11-11T12:27:00Z', '2026-11-11T11:29:00Z',
    '2026-11-11T10:42:00Z', '2026-11-08T10:19:00Z', '2026-10-01T10:19:00Z']
  const chats = []
  for (const [i, count] of [0, 1, 7, 10, 99, 103].entries()) {
    const timestamp = Date.parse(times[i]) / 1000
    const root = { id: `aligned-${i}`, title: i === 1 ? longTitle : `Aligned chat ${i}`,
      created_at: timestamp, updated_at: timestamp, last_activity_at: timestamp, unread: i === 1 }
    chats.push(root)
    for (let child = 0; child < count; child++) chats.push({
      ...root, id: `${root.id}-child-${child}`, title: `Agent ${child}`, delegated_parent_chat_id: root.id,
      unread: false, answering: i === 1 && child === 0
    })
  }
  await context.route('**/api/chats', route => route.fulfill({ json: { chats } }))
  return chats
}

async function expectAlignedMetadata (page) {
  await expect(page.locator('.side__list .chat-list-item').first()).toBeVisible()
  const rows = await page.locator('.side__list .chat-list-item').evaluateAll(elements => elements.map(el => {
    const bounds = selector => {
      const { x, y, width, height } = el.querySelector(selector).getBoundingClientRect()
      return { x, y, width, height }
    }
    return { square: bounds('.chat-status'), slot: bounds('.chat-list-item__status'),
      trailing: bounds('.chat-list-item__trailing'), title: bounds('.row-item__title'),
      badge: el.querySelector('.chat-list-item__count') ? bounds('.chat-list-item__count') : null }
  }))
  expect(rows.length).toBeGreaterThan(1)
  for (const row of rows) {
    expect(row.square.x).toBeCloseTo(rows[0].square.x)
    expect(row.square.width).toBeCloseTo(rows[0].square.width)
    expect(row.trailing.x).toBeCloseTo(rows[0].trailing.x)
    expect(row.trailing.width).toBeCloseTo(rows[0].trailing.width)
    expect(row.title.width).toBeGreaterThan(0)
    expect(row.title.x + row.title.width).toBeLessThan(row.slot.x)
    if (row.badge) {
      expect(row.badge.x).toBeGreaterThanOrEqual(row.slot.x)
      expect(row.badge.x + row.badge.width).toBeLessThanOrEqual(row.slot.x + row.slot.width + .1)
      expect(row.badge.x + row.badge.width).toBeLessThan(row.trailing.x)
    }
  }
  expect(await page.locator('.side__list').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
}

for (const [width, theme, interfaceSize] of [[1440, 'orbit', 100], [1440, 'daylight', 150], [320, 'orbit', 100], [320, 'daylight', 150]]) {
  test(`squares align across counts and timestamp widths at ${width}px in ${theme} at ${interfaceSize}%`, async ({ page, context }) => {
    await alignmentFixture(page, context, { theme, interfaceSize })
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/chats')
    await expect(page.locator('.chat-list-item')).toHaveCount(6)
    await expectAlignedMetadata(page)
    const badges = page.locator('.chat-list-item__count')
    await expect(badges).toHaveText(['1', '7', '10', '99', '99+'])
    const large = page.locator('a[href="/chats/aligned-5"]').locator('..')
    await expect(large.getByRole('button', { name: 'Expand agent chats for Aligned chat 5 (103)', exact: true })).toHaveAttribute('title', /^103 agent chats/)
    await expect(badges.first()).toHaveCSS('animation-name', 'none')
    const root = page.locator('a[href="/chats/aligned-1"]').locator('..')
    const before = await root.locator('.row-item__title').boundingBox()
    await root.hover()
    await expectAlignedMetadata(page)
    expect((await root.locator('.row-item__title').boundingBox()).width).toBeCloseTo(before.width)
    await root.getByRole('button', { name: `Expand agent chats for ${longTitle} (1)`, exact: true }).click()
    await expect(page.locator('.chat-list-item')).toHaveCount(7)
    await expectAlignedMetadata(page)
    await expect(page).toHaveURL(/\/chats$/)
    await page.screenshot({ path: `/tmp/chat-row-alignment-${width}-${theme}.png` })
    for (const time of await page.locator('.row-item__time').all()) {
      await expect(time).toHaveCSS('font-variant-numeric', 'tabular-nums')
      expect(await time.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    }
  })
}

test('changing a tree count or removing its last agent never shifts the tile or title', async ({ page, context }) => {
  const chats = await alignmentFixture(page, context)
  await page.goto('/chats')
  const row = page.locator('a[href="/chats/aligned-1"]').locator('..')
  const before = await row.locator('.row-item__title').boundingBox()
  const child = chats.find(chat => chat.delegated_parent_chat_id === 'aligned-1')
  for (let i = 1; i < 10; i++) chats.push({ ...child, id: `new-agent-${i}` })
  await page.evaluate(async () => (await import('/src/store.js')).actions.loadChats())
  await expect(row.locator('.chat-list-item__count')).toHaveText('10')
  await expectAlignedMetadata(page)
  expect((await row.locator('.row-item__title').boundingBox()).width).toBeCloseTo(before.width)
  for (let i = chats.length - 1; i >= 0; i--) if (chats[i].delegated_parent_chat_id === 'aligned-1') chats.splice(i, 1)
  await page.evaluate(async () => (await import('/src/store.js')).actions.loadChats())
  await expect(row.locator('.chat-list-item__count, .chat-status__lines, .chat-list-item__tree')).toHaveCount(0)
  await expectAlignedMetadata(page)
  expect((await row.locator('.row-item__title').boundingBox()).width).toBeCloseTo(before.width)
})

test.describe('touch actions', () => {
  test.use({ hasTouch: true })
  test('tree badges share the standalone tile column on touch at 150%', async ({ page, context }) => {
    await alignmentFixture(page, context, { interfaceSize: 150 })
    await page.setViewportSize({ width: 320, height: 1000 })
    await page.goto('/chats')
    await expectAlignedMetadata(page)
    for (const toggle of await page.locator('.chat-list-item__tree').all()) {
      const box = await toggle.boundingBox()
      expect(box.width).toBeGreaterThanOrEqual(44)
      expect(box.height).toBeGreaterThanOrEqual(44)
    }
  })
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
