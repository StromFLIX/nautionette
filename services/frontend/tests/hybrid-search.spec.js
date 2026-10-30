import { test, expect } from '@playwright/test'
import { mockDesign } from './design-fixture.js'

const palette = page => page.getByRole('dialog', { name: 'Search chats and settings', exact: true })
const search = page => palette(page).getByRole('combobox', { name: 'Search chats and settings' })
const result = (extra = {}) => ({ chat_id: 'alpha', title: 'Design review', message_id: 'question', excerpt: 'A passage from an older message', match: 'keyword', ...extra })
const response = (results, state = 'ready') => ({ results, semantic: { state, pending: state === 'indexing' ? 100 : 0 } })

async function open (page) {
  await expect(page.getByRole('button', { name: 'Expand chat search', exact: true })).toBeVisible()
  await page.keyboard.press('Control+k')
  await expect(search(page)).toBeFocused()
}

test('full-history results jump to an older message, focus it and retain drafts', async ({ page, context }) => {
  const state = await mockDesign(context)
  state.data.messages[0].content = 'Archival passage from the first message'
  state.data.messages.push(...Array.from({ length: 20 }, (_, i) => ({ id: `later-${i}`, role: 'assistant', content: `Later message ${i}\n\n${'A long answer. '.repeat(60)}`, meta: {}, created_at: Date.now() / 1000 })))
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/chats/alpha')
  const composer = page.getByRole('textbox', { name: 'Message', exact: true })
  await composer.fill('Draft stays here')
  await open(page)
  await search(page).fill('archival')
  await expect(palette(page)).toContainText('Archival passage')
  await expect(palette(page)).toContainText('Keyword search')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/chats\/alpha#message-question$/)
  await expect(page.locator('#message-question')).toBeFocused()
  await expect(page.locator('#message-question')).toBeInViewport()
  await expect(composer).toHaveValue('Draft stays here')
  // A repeated snapshot must not jump to the bottom after following the link.
  await page.waitForTimeout(1200)
  await expect(page.locator('#message-question')).toBeInViewport()
  // Reopening the exact same result must still leave Outputs and focus the match,
  // rather than having the dialog restore focus to the previous tab/composer.
  await page.getByRole('tab', { name: /^Outputs/ }).click()
  await open(page)
  await search(page).fill('archival')
  await expect(palette(page)).toContainText('Archival passage')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('tab', { name: 'Chat', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(palette(page)).toHaveCount(0)
  await expect(page.locator('#message-question')).toBeFocused()
  await expect(page.locator('#message-question')).toBeInViewport()
  await expect(composer).toHaveValue('Draft stays here')
  expect(errors).toEqual([])
})

test('semantic-only matches show related excerpts and indexing progress', async ({ page, context }) => {
  await mockDesign(context)
  await context.route('**/api/search/chats?*', route => route.fulfill({ json: response([result({ match: 'semantic', excerpt: 'Cut our cloud costs' })], 'indexing') }))
  await page.goto('/chats')
  await open(page)
  await search(page).fill('reducing infrastructure expenses')
  await expect(palette(page).getByRole('option', { name: 'Design review', exact: true })).toBeVisible()
  await expect(palette(page)).toContainText('Related · Cut our cloud costs')
  await expect(palette(page)).toContainText('Hybrid search · indexing history')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/chats\/alpha#message-question$/)
  await expect(page.locator('#message-question')).toBeFocused()
})

test('queries debounce and late responses cannot overwrite a newer query or scope', async ({ page, context }) => {
  await mockDesign(context)
  const requests = []
  let releaseOld
  const held = new Promise(resolve => { releaseOld = resolve })
  await context.route('**/api/search/chats?*', async route => {
    const query = new URL(route.request().url()).searchParams.get('q')
    requests.push(query)
    if (query === 'older') await held
    await route.fulfill({ json: response([result({ title: query })]) }).catch(() => {})
  })
  await page.goto('/chats')
  await open(page)
  await search(page).fill('o')
  await search(page).fill('ol')
  await search(page).fill('older')
  await expect.poll(() => requests).toEqual(['older'])
  await search(page).fill('newer')
  await expect(palette(page).getByRole('option', { name: 'newer', exact: true })).toBeVisible()
  releaseOld()
  await page.waitForTimeout(200)
  await expect(palette(page).getByRole('option', { name: 'older', exact: true })).toHaveCount(0)
  await palette(page).getByRole('button', { name: 'Settings', exact: true }).click()
  await search(page).fill('default model')
  await expect(palette(page).getByRole('option', { name: 'Default model', exact: true })).toBeVisible()
  await page.waitForTimeout(250)
  expect(requests).toEqual(['older', 'newer'])
})

test('errors preserve the local fallback and clearing a query restores recent chats', async ({ page, context }) => {
  await mockDesign(context)
  await context.route('**/api/search/chats?*', route => route.fulfill({ status: 503, json: { detail: 'unavailable' } }))
  await page.goto('/chats')
  await open(page)
  await search(page).fill('Design')
  await expect(palette(page).getByRole('alert')).toContainText('Full-history search unavailable')
  await expect(palette(page).getByRole('option', { name: 'Design review', exact: true })).toBeVisible()
  await expect(palette(page)).toContainText('Titles & latest previews')
  await search(page).fill('')
  await expect(palette(page).getByRole('alert')).toHaveCount(0)
  await expect(palette(page)).toContainText('Recent chats')
})

test('keyword results remain available while the semantic model loads and excerpts are plain text', async ({ page, context }) => {
  await mockDesign(context)
  await context.route('**/api/search/chats?*', route => route.fulfill({ json: response([result({ excerpt: '<img src=x onerror=alert(1)> Needle' })], 'loading') }))
  await page.goto('/chats')
  await open(page)
  await search(page).fill('needle')
  await expect(palette(page)).toContainText('Keyword search · loading semantic model')
  await expect(palette(page)).toContainText('<img src=x onerror=alert(1)> Needle')
  await expect(palette(page).locator('img')).toHaveCount(0)
})
