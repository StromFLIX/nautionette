import { test, expect } from '@playwright/test'
import { mockDesign } from './design-fixture.js'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'

async function fixture (context) {
  await mockDesign(context)
  const now = Date.now() / 1000
  const chats = [
    { id: 'root', title: 'Build the website', created_at: now - 100000 },
    { id: 'branding', title: 'Design website branding', delegated_parent_chat_id: 'root', created_at: now - 60 },
    { id: 'research', title: 'Research accessible colours', delegated_parent_chat_id: 'branding', created_at: now, answering: true, unread: true },
    { id: 'layout', title: 'Implement responsive layout', delegated_parent_chat_id: 'root', created_at: now - 120 },
    { id: 'unrelated', title: 'Plan next release', created_at: now - 180 }
  ].map(chat => ({ ...chat, updated_at: chat.created_at }))
  await context.route('**/api/chats', route => route.fulfill({ json: { chats } }))
  await context.route('**/api/chats/*', route => {
    const id = new URL(route.request().url()).pathname.split('/').pop()
    return route.fulfill({ json: { chat: chats.find(chat => chat.id === id), messages: [], active_turn: null } })
  })
  return chats
}

for (const [width, theme, interfaceSize] of [[1440, 'orbit', 100], [320, 'orbit', 100], [1440, 'daylight', 150], [320, 'daylight', 150]]) {
  test(`agent chat trees collapse by default, reveal all levels and fit at ${width}px in ${theme} at ${interfaceSize}%`, async ({ page, context }) => {
    await fixture(context)
    await context.addInitScript(({ key, theme, interfaceSize }) => {
      localStorage.setItem(key, JSON.stringify({ theme, interfaceSize }))
    }, { key: PREFERENCES_KEY, theme, interfaceSize })
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/chats')
    const list = page.locator('.side__list')
    const titles = list.locator('.row-item__title')
    await expect(titles).toHaveText(['Build the website', 'Plan next release'])
    const rootToggle = page.getByRole('button', { name: 'Expand agent chats for Build the website (3)', exact: true })
    await expect(rootToggle).toHaveAttribute('aria-expanded', 'false')
    await expect(rootToggle).toHaveAccessibleDescription('Agent chats active · Unread agent replies')
    await expect(rootToggle.locator('.chat-list-item__count')).toHaveText('3')
    await expect(rootToggle.locator('.chat-status__lines')).toHaveCount(1)
    await expect(rootToggle.locator('.chat-status')).toHaveCSS('animation-name', 'chat-status-pulse')
    await expect(list.locator('.chat-status rect')).toHaveCount(2)
    await expect(list.locator('.chat-status circle, .row-item__unread, .chat-tree__disclosure')).toHaveCount(0)
    const parentTitleBox = await list.locator('a[href="/chats/root"] .row-item__title').boundingBox()
    const toggleBox = await rootToggle.boundingBox()
    expect(toggleBox.x).toBeGreaterThan(parentTitleBox.x + parentTitleBox.width)
    expect(toggleBox.y + toggleBox.height / 2).toBeCloseTo(parentTitleBox.y + parentTitleBox.height / 2, 0)
    const rootRow = list.locator('.chat-list-item').first()
    await expect(rootRow.locator('a')).toHaveAccessibleDescription('Agent chats active · Unread agent replies')
    await page.screenshot({ path: `/tmp/chat-tree-collapsed-${width}-${theme}.png` })
    await expect(rootToggle.locator('.chat-status')).toHaveAttribute('aria-label', 'Agent chats active · Unread agent replies')
    const standalone = list.locator('.chat-list-item').last()
    await expect(standalone.locator('.chat-status__lines')).toHaveCount(0)
    await expect(standalone.locator('.chat-status rect')).toHaveCount(1)
    const titleWidth = parentTitleBox.width
    await rootRow.hover()
    expect((await rootRow.locator('.row-item__title').boundingBox()).width).toBeCloseTo(titleWidth)
    await rootToggle.focus()
    await page.keyboard.press('Enter')
    await expect(titles).toHaveText(['Build the website', 'Design website branding', 'Implement responsive layout', 'Plan next release'])
    await page.getByRole('button', { name: 'Expand agent chats for Design website branding (1)', exact: true }).click()
    await expect(titles).toHaveText(['Build the website', 'Design website branding', 'Research accessible colours', 'Implement responsive layout', 'Plan next release'])
    await expect(list.locator('a[href="/chats/research"]').locator('..')).toHaveAttribute('data-tree-depth', '2')
    const family = list.getByRole('group', { name: 'Agent chats for Build the website', exact: true })
    await expect(family.locator('.row-item__title')).toHaveText(['Design website branding', 'Research accessible colours', 'Implement responsive layout'])
    await expect(family.getByRole('group', { name: 'Agent chats for Design website branding', exact: true })).toBeVisible()
    await expect(family.locator('a[href="/chats/unrelated"]')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Collapse agent chats for Build the website (3)', exact: true })).toHaveAttribute('aria-controls', await family.getAttribute('id'))
    const rail = family.locator(':scope > .chat-tree').last()
    expect(await rail.evaluate(el => getComputedStyle(el, '::before').borderInlineStartWidth)).toBe('1px')
    expect(await rail.evaluate(el => getComputedStyle(el, '::after').borderBlockStartWidth)).toBe('1px')
    await expect(page).toHaveURL(/\/chats$/)
    expect(await list.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/chat-tree-${width}-${theme}.png` })
    await page.getByRole('button', { name: 'Collapse agent chats for Build the website (3)', exact: true }).click()
    await expect(titles).toHaveText(['Build the website', 'Plan next release'])
    await page.reload()
    await expect(titles).toHaveText(['Build the website', 'Plan next release'])
  })
}

test('search reveals matching descendants with ancestors and direct links reveal the selected chat', async ({ page, context }) => {
  await fixture(context)
  await page.goto('/chats')
  const titles = page.locator('.side__list .row-item__title')
  await page.getByRole('textbox', { name: 'Search chats', exact: true }).fill('accessible colours')
  await expect(titles).toHaveText(['Build the website', 'Design website branding', 'Research accessible colours'])
  await page.getByRole('textbox', { name: 'Search chats', exact: true }).clear()
  await expect(titles).toHaveText(['Build the website', 'Plan next release'])
  await page.goto('/chats/research')
  await expect(page.locator('.side__list a[href="/chats/research"]')).toHaveClass(/row-item--active/)
  await expect(page.getByRole('button', { name: 'Collapse agent chats for Build the website (3)', exact: true })).toHaveAttribute('aria-expanded', 'true')
  await page.getByRole('button', { name: 'Collapse agent chats for Build the website (3)', exact: true }).click()
  await page.evaluate(async () => (await import('/src/store.js')).actions.loadChats())
  await expect(titles).toHaveText(['Build the website', 'Plan next release'])
})

test('recent idle descendants and selected old descendants keep their ancestors visible', async ({ page, context }) => {
  const chats = await fixture(context)
  const child = chats.find(chat => chat.id === 'research')
  child.answering = false
  child.unread = false
  await page.goto('/chats')
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  await page.getByRole('group', { name: 'Show chats active within', exact: true }).getByRole('button', { name: '1h', exact: true }).click()
  await expect(page.locator('.side__list a[href="/chats/root"]')).toBeVisible()
  for (const chat of chats) chat.updated_at = chat.created_at = 1
  await page.goto('/chats/research')
  await expect(page.locator('.side__list a[href="/chats/research"]')).toHaveClass(/row-item--active/)
  await expect(page.locator('.side__list a[href="/chats/root"]')).toBeVisible()
})

test('old parent stays visible when its descendant is active and grouping keeps the family together', async ({ page, context }) => {
  await fixture(context)
  await page.goto('/chats')
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  await page.getByRole('group', { name: 'Show chats active within', exact: true }).getByRole('button', { name: '1h', exact: true }).click()
  await expect(page.locator('.side__list a[href="/chats/root"]')).toBeVisible()
  await page.getByRole('group', { name: 'Group chats by', exact: true }).getByRole('button', { name: 'Activity', exact: true }).click()
  await expect(page.locator('.side__group .truncate')).toHaveText(['Active', 'Inactive'])
  await expect(page.locator('.side__chat-group').first().locator('.row-item__title')).toHaveText(['Build the website'])
  await page.getByRole('button', { name: 'Expand agent chats for Build the website (3)', exact: true }).click()
  await expect(page.locator('.side__chat-group').first().locator('.row-item__title')).toHaveText(['Build the website', 'Design website branding', 'Implement responsive layout'])
})

for (const preferences of [{}, { chatListAnimation: false }, { motion: 'reduced' }]) {
  test(`tree squares keep activity and unread independent with ${JSON.stringify(preferences)}`, async ({ page, context }) => {
    const chats = await fixture(context)
    await context.addInitScript(({ key, preferences }) => {
      localStorage.setItem(key, JSON.stringify(preferences))
    }, { key: PREFERENCES_KEY, preferences })
    await page.goto('/chats')
    const row = page.locator('a[href="/chats/root"]').locator('..')
    const square = row.locator('.chat-status')
    const animation = preferences.chatListAnimation === false || preferences.motion === 'reduced' ? 'none' : 'chat-status-pulse'
    await expect(square).toHaveCSS('animation-name', animation)
    await expect(square).not.toHaveCSS('fill', 'none')
    await expect(row.locator('a')).toHaveAccessibleDescription('Agent chats active · Unread agent replies')
    await expect(row.locator('.row-item__title')).toHaveCSS('font-weight', '500')
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await expect(square).toHaveCSS('animation-name', 'none')
    await expect(square).toHaveClass(/chat-status--active/)
    chats.find(chat => chat.id === 'research').answering = false
    await page.evaluate(async () => (await import('/src/store.js')).actions.loadChats())
    await expect(square).not.toHaveClass(/chat-status--active/)
    await expect(row.locator('a')).toHaveAccessibleDescription('Inactive · Unread agent replies')
    await expect(row.locator('.row-item__title')).toHaveCSS('font-weight', '500')
    chats.splice(1, 3) // Removing the final children changes the same tile to a plain square.
    await page.evaluate(async () => (await import('/src/store.js')).actions.loadChats())
    await expect(row.locator('.chat-list-item__tree, .chat-status__lines, .chat-list-item__count')).toHaveCount(0)
    await expect(row.locator('.chat-status rect')).toHaveCount(1)
    await expect(row.locator('a')).toHaveAccessibleDescription('Inactive')
  })
}

for (const width of [320, 1440]) {
  test(`multi-digit tree counts fit a long title at 150% and ${width}px`, async ({ page, context }) => {
    const chats = await fixture(context)
    chats[0].title = 'Design Denkzeug website branding and refine the complete navigation'
    for (let i = 0; i < 100; i++) chats.push({
      id: `agent-${i}`, title: `Agent ${i}`, delegated_parent_chat_id: 'root',
      created_at: chats[0].created_at, updated_at: chats[0].updated_at
    })
    await context.addInitScript(key => {
      localStorage.setItem(key, JSON.stringify({ interfaceSize: 150, sideWidth: 260 }))
    }, PREFERENCES_KEY)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats')
    const row = page.locator('a[href="/chats/root"]').locator('..')
    const title = await row.locator('.row-item__title').boundingBox()
    const toggle = row.getByRole('button', { name: `Expand agent chats for ${chats[0].title} (103)`, exact: true })
    const button = await toggle.boundingBox()
    const trailing = await row.locator('.chat-list-item__trailing').boundingBox()
    expect(title.width).toBeGreaterThan(0)
    expect(title.x + title.width).toBeLessThan(button.x)
    expect(button.x + button.width).toBeLessThan(trailing.x)
    expect(await page.locator('.side__list').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await row.locator('a').focus()
    await page.keyboard.press('Tab')
    await expect(toggle).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(row.getByRole('button', { name: `Options for ${chats[0].title}`, exact: true })).toBeFocused()
    expect((await row.locator('.row-item__title').boundingBox()).width).toBeCloseTo(title.width)
  })
}
