import { test, expect } from '@playwright/test'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockDesign } from './design-fixture.js'

const grouping = page => page.getByRole('group', { name: 'Group chats by', exact: true })
const activity = page => page.getByRole('group', { name: 'Show chats active within', exact: true })
const titles = page => page.locator('.side__list .row-item__title')

async function fixture (context) {
  await mockDesign(context)
  const now = Date.now() / 1000
  const chats = [
    { id: 'recent', title: 'Recent project chat', project_ids: ['project'], updated_at: now - 60 },
    { id: 'aging', title: 'Nearly an hour old', project_ids: ['project'], updated_at: now - 3590 },
    { id: 'old', title: 'Old project chat', project_ids: ['project'], updated_at: now - 7200 },
    { id: 'other', title: 'Other project chat', project_ids: ['other'], updated_at: now - 120 },
    { id: 'unread', title: 'Unread old chat', project_ids: [], updated_at: now - 10800, unread: true },
    { id: 'running', title: 'Running old chat', project_ids: [], updated_at: now - 10800, answering: true }
  ].map((chat, index) => ({ ...chat, created_at: chat.updated_at - index }))
  await context.route('**/api/chats', route => route.fulfill({ json: { chats } }))
  await context.route('**/api/chats/*', route => {
    const id = new URL(route.request().url()).pathname.split('/').pop()
    return route.fulfill({ json: { chat: chats.find(chat => chat.id === id), messages: [], active_turn: null } })
  })
  await context.route('**/api/projects', route => route.fulfill({ json: { projects: [
    { id: 'project', full_name: 'StromFLIX/nautionette' },
    { id: 'other', full_name: 'StromFLIX/other' }
  ] } }))
  return chats
}

async function selectView (page) {
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  await grouping(page).getByRole('button', { name: 'Project', exact: true }).click()
  await activity(page).getByRole('button', { name: '1h', exact: true }).click()
}

async function expectView (page) {
  await expect(grouping(page).getByRole('button', { name: 'Project', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(activity(page).getByRole('button', { name: '1h', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.side__group .truncate')).toHaveText(['StromFLIX/nautionette', 'StromFLIX/other', 'No project'])
  await expect(titles(page)).not.toContainText(['Old project chat'])
  await expect(page.getByRole('button', { name: 'Show 1 older', exact: true })).toBeVisible()
}

test('group controls and workspace settings offer the same useful views', async ({ page, context }) => {
  await fixture(context)
  await page.goto('/chats')
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  await expect(grouping(page).getByRole('button')).toHaveText(['All', 'Date', 'Activity', 'Project'])
  const settings = await context.newPage()
  await settings.goto('/settings/workspace')
  const select = settings.getByLabel('Group chats by', { exact: true })
  await expect(select.locator('option')).toHaveText(['No grouping', 'Date', 'Activity', 'Project'])
  for (const [label, value] of [['Date', 'date'], ['Activity', 'activity']]) {
    await grouping(page).getByRole('button', { name: label, exact: true }).click()
    await expect(select).toHaveValue(value)
    await page.reload()
    await page.getByRole('button', { name: 'Group', exact: true }).click()
    await expect(grouping(page).getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-pressed', 'true')
  }
  await settings.getByRole('button', { name: 'Reset workspace', exact: true }).click()
  await expect(grouping(page).getByRole('button', { name: 'All', exact: true })).toHaveAttribute('aria-pressed', 'true')
})

for (const width of [1440, 320]) {
  test(`single-line rows and grouping controls fit long titles at 150% and ${width}px`, async ({ page, context }) => {
    const chats = await fixture(context)
    chats[0].title = 'Refine cashflow layout and make the project comparison easier to read without expanding the sidebar'
    chats[0].answering = true
    chats[0].unread = true
    await context.addInitScript(key => {
      localStorage.setItem(key, JSON.stringify({ interfaceSize: 150, sideWidth: 260, theme: 'nebula', chatGroupBy: 'project' }))
    }, PREFERENCES_KEY)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats')
    await page.getByRole('button', { name: 'Group', exact: true }).click()
    for (const button of await grouping(page).getByRole('button').all()) {
      await expect(button).toBeInViewport({ ratio: 1 })
      expect(await button.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    }
    const row = page.locator('a[href="/chats/recent"]')
    const title = row.locator('.row-item__title')
    await expect(title).toHaveCSS('text-overflow', 'ellipsis')
    await expect(title).toHaveAttribute('title', chats[0].title)
    const time = await row.locator('.row-item__time').boundingBox()
    const titleBox = await title.boundingBox()
    const icon = await row.locator('.chat-status').boundingBox()
    expect(icon.x + icon.width).toBeLessThan(titleBox.x)
    expect(titleBox.x + titleBox.width).toBeLessThan(time.x)
    expect(titleBox.y + titleBox.height / 2).toBeCloseTo(time.y + time.height / 2, 0)
    const box = await row.boundingBox()
    expect(box.height).toBeLessThan(56)
    if (width === 320) expect(box.height).toBeGreaterThanOrEqual(44)
    expect(await page.locator('.side__list').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/nautionette-compact-chat-list-${width}.png` })
  })
}

test('activity groups distinguish active, unread and inactive without message previews', async ({ page, context }) => {
  const chats = await fixture(context)
  const running = chats.find(chat => chat.id === 'running')
  chats[0].answering = true
  chats[0].last_message = { role: 'assistant', preview: 'This preview must stay out of the list' }
  await page.goto('/chats')
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  await grouping(page).getByRole('button', { name: 'Activity', exact: true }).click()
  await expect(page.locator('.side__group .truncate')).toHaveText(['Active', 'Unread', 'Inactive'])
  const active = page.locator('.side__chat-group').filter({ has: page.locator('.side__group .truncate', { hasText: /^Active$/ }) })
  await expect(active.locator('.row-item__title')).toHaveText(['Recent project chat', 'Running old chat'])
  await expect(page.locator('.side__list')).not.toContainText('This preview must stay out of the list')
  await expect(page.locator('.side__list')).not.toContainText('No messages yet')
  await expect(page.locator('.side__list .avatar, .side__list .row-item__sub, .side__list .row-item__activity')).toHaveCount(0)
  running.answering = false
  running.unread = true
  await page.evaluate(async () => (await import('/src/store.js')).actions.loadChats())
  await expect(active.locator('.row-item__title')).toHaveText(['Recent project chat'])
  const unread = page.locator('.side__chat-group').filter({ has: page.locator('.side__group .truncate', { hasText: /^Unread$/ }) })
  await expect(unread.locator('.row-item__title')).toHaveText(['Unread old chat', 'Running old chat'])
  await expect(unread.locator('.chat-status')).toHaveCount(2)
  await expect(unread.locator('.chat-status--active')).toHaveCount(0)
})

test.describe('local date grouping', () => {
  test.use({ timezoneId: 'America/Los_Angeles', locale: 'en-US' })
  test('date groups follow conversation start dates rather than UTC or latest activity', async ({ page, context }) => {
    const chats = await fixture(context)
    chats.splice(0, chats.length,
      { id: 'first', title: 'Before local midnight', created_at: Date.parse('2026-09-30T23:59:00-07:00') / 1000, updated_at: Date.now() / 1000 },
      { id: 'second', title: 'After local midnight', created_at: Date.parse('2026-10-01T00:01:00-07:00') / 1000 },
      { id: 'third', title: 'Same local day, different UTC day', created_at: Date.parse('2026-09-30T00:01:00-07:00') / 1000 }
    )
    await page.goto('/chats')
    await page.getByRole('button', { name: 'Group', exact: true }).click()
    await grouping(page).getByRole('button', { name: 'Date', exact: true }).click()
    await expect(page.locator('.side__group .truncate')).toHaveText(['Oct 1, 2026', 'Sep 30, 2026'])
    await expect(page.locator('.side__group-count')).toHaveText(['1', '2'])
    await expect(titles(page)).toHaveText(['After local midnight', 'Before local midnight', 'Same local day, different UTC day'])
    await page.getByRole('textbox', { name: 'Search chats', exact: true }).fill('Before local')
    await expect(page.locator('.side__group-count')).toHaveText('1')
    await expect(titles(page)).toHaveText(['Before local midnight'])
  })
})

test('project grouping and activity filtering survive navigation, reload and browser tabs', async ({ page, context }) => {
  await fixture(context)
  await page.goto('/chats')
  await selectView(page)
  await expectView(page)
  const nav = page.getByRole('navigation', { name: 'Main navigation' })
  for (const section of ['Workflows', 'Runs', 'Chats']) {
    await nav.getByRole('link', { name: section, exact: true }).click()
    await expect(page.locator('.side__title')).toHaveText(section)
  }
  await expectView(page)
  await nav.getByRole('link', { name: 'Settings', exact: true }).click()
  await expect(page.locator('#shell-sidebar')).toHaveCount(0)
  await nav.getByRole('link', { name: 'Chats', exact: true }).click()
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  await expectView(page)
  await page.reload()
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  await expectView(page)
  const second = await context.newPage()
  await second.goto('/chats')
  await second.getByRole('button', { name: 'Group', exact: true }).click()
  await expectView(second)
  await page.bringToFront()
  await expectView(page)
})

for (const resume of ['visibilitychange', 'focus', 'pageshow', 'navigation', 'interval']) {
  test(`activity window stays current after ${resume} without reselecting the filter`, async ({ page, context }) => {
    await fixture(context)
    await page.clock.install()
    await page.goto('/chats')
    await selectView(page)
    await expect(titles(page)).toContainText(['Nearly an hour old'])
    // A background/suspended tab may resume before its next interval callback.
    if (resume === 'interval') await page.clock.runFor(30000)
    else {
      await page.clock.setSystemTime(Date.now() + 20000)
      if (resume === 'navigation') {
        const nav = page.getByRole('navigation', { name: 'Main navigation' })
        await nav.getByRole('link', { name: 'Workflows', exact: true }).click()
        await expect(page.locator('.side__title')).toHaveText('Workflows')
        await nav.getByRole('link', { name: 'Chats', exact: true }).click()
      } else {
        await page.evaluate(event => (event === 'visibilitychange' ? document : window).dispatchEvent(new Event(event)), resume)
      }
    }
    await expect(titles(page)).not.toContainText(['Nearly an hour old'])
    await expect(page.getByRole('button', { name: 'Show 2 older', exact: true })).toBeVisible()
    await expect(page.locator('.side__group .truncate')).toHaveText(['StromFLIX/nautionette', 'StromFLIX/other', 'No project'])
    await expect(titles(page)).toContainText(['Unread old chat', 'Running old chat'])
    await expect(activity(page).getByRole('button', { name: '1h', exact: true })).toHaveAttribute('aria-pressed', 'true')
  })
}

test('a delayed cross-tab preference event cannot overwrite the latest selected view', async ({ page, context }) => {
  await fixture(context)
  await page.goto('/chats')
  await selectView(page)
  await expectView(page)
  // Storage events can be queued while a tab is backgrounded; their payload is
  // a historical snapshot, not necessarily the current value in localStorage.
  await page.evaluate(key => {
    const saved = JSON.parse(localStorage.getItem(key))
    window.dispatchEvent(new StorageEvent('storage', {
      key, storageArea: localStorage,
      newValue: JSON.stringify({ ...saved, chatGroupBy: 'none', chatActiveMinutes: 0 })
    }))
  }, PREFERENCES_KEY)
  await expectView(page)
})

test('an unrelated local edit preserves newer grouping and filter preferences from another tab', async ({ page, context }) => {
  await fixture(context)
  await page.goto('/chats')
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  // The shared value has changed, but this tab has not processed its event yet.
  await page.evaluate(key => localStorage.setItem(key, JSON.stringify({ chatGroupBy: 'project', chatActiveMinutes: 60 })), PREFERENCES_KEY)
  await page.getByRole('separator', { name: 'Resize sidebar' }).press('ArrowRight')
  await expectView(page)
  await expect(page.getByRole('separator', { name: 'Resize sidebar' })).toHaveAttribute('aria-valuenow', '330')
  expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key)), PREFERENCES_KEY)).toMatchObject({
    chatGroupBy: 'project', chatActiveMinutes: 60, sideWidth: 330
  })
})

test('a local selection can replace malformed saved preferences', async ({ page, context }) => {
  await fixture(context)
  await page.goto('/chats')
  await page.evaluate(key => localStorage.setItem(key, '{invalid'), PREFERENCES_KEY)
  await selectView(page)
  await expectView(page)
  await page.reload()
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  await expectView(page)
})

test('receiving preferences from another tab does not write them back to storage', async ({ page, context }) => {
  await fixture(context)
  await page.goto('/chats')
  await page.getByRole('button', { name: 'Group', exact: true }).click()
  await page.evaluate(key => {
    window.preferenceWrites = []
    const setItem = Storage.prototype.setItem
    Storage.prototype.setItem = function (name, value) {
      if (name === key) window.preferenceWrites.push(value)
      return setItem.call(this, name, value)
    }
  }, PREFERENCES_KEY)
  const second = await context.newPage()
  await second.goto('/chats')
  await selectView(second)
  await expectView(page)
  expect(await page.evaluate(() => window.preferenceWrites)).toEqual([])
})
