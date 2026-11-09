import { test, expect } from '@playwright/test'
import { mockDesign } from './design-fixture.js'

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

for (const width of [1440, 320]) {
  test(`agent chat trees collapse by default, reveal all levels and fit at ${width}px`, async ({ page, context }) => {
    await fixture(context)
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/chats')
    const list = page.locator('.side__list')
    const titles = list.locator('.row-item__title')
    await expect(titles).toHaveText(['Build the website', 'Plan next release'])
    const rootToggle = page.getByRole('button', { name: 'Expand agent chats for Build the website (3)', exact: true })
    await expect(rootToggle).toHaveAttribute('aria-expanded', 'false')
    await expect(list.locator('a[href="/chats/root"] .chat-status')).toHaveAttribute('aria-label', 'Agent chats active')
    await expect(list.getByLabel('Unread agent replies', { exact: true })).toBeVisible()
    await rootToggle.focus()
    await page.keyboard.press('Enter')
    await expect(titles).toHaveText(['Build the website', 'Design website branding', 'Implement responsive layout', 'Plan next release'])
    await page.getByRole('button', { name: 'Expand agent chats for Design website branding (1)', exact: true }).click()
    await expect(titles).toHaveText(['Build the website', 'Design website branding', 'Research accessible colours', 'Implement responsive layout', 'Plan next release'])
    await expect(list.locator('a[href="/chats/research"]').locator('..')).toHaveAttribute('data-tree-depth', '2')
    expect(await list.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/chat-tree-${width}.png` })
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
