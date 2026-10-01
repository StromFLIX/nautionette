import { test, expect } from '@playwright/test'
import { PREFERENCES_KEY } from '../src/preferences-schema.js'
import { mockDesign } from './design-fixture.js'

const titles = page => page.locator('.side__list .row-item__title')

async function fixture (context) {
  const state = await mockDesign(context)
  const now = Date.now() / 1000
  const chats = [
    { ...state.data.chat, id: 'working', title: 'Agent using tools', project_ids: ['tools'], answering: true,
      created_at: now - 18000, updated_at: now - 60, last_message_at: now - 300, last_user_message_at: now - 600 },
    { ...state.data.chat, id: 'replied', title: 'Agent replied', project_ids: ['reply'], answering: false,
      created_at: now - 7200, updated_at: now - 120, last_message_at: now - 120, last_user_message_at: now - 500 },
    { ...state.data.chat, title: 'User just asked', project_ids: ['user'], answering: false,
      created_at: now - 600, updated_at: now - 180, last_message_at: now - 180, last_user_message_at: now - 180 }
  ]
  Object.assign(state.data.chat, chats[2])
  await context.route('**/api/chats', route => route.fulfill({ json: { chats } }))
  await context.route('**/api/projects', route => route.fulfill({ json: { projects: [
    { id: 'tools', full_name: 'Project tools' },
    { id: 'reply', full_name: 'Project reply' },
    { id: 'user', full_name: 'Project user' }
  ] } }))
  await context.addInitScript(() => {
    const NativeEventSource = window.EventSource
    window.EventSource = class extends NativeEventSource {
      constructor (url, options) {
        super(url, options)
        if (new URL(url, location.href).pathname === '/api/events') window.globalEvents = this
      }
    }
  })
  return chats
}

async function emit (page, kind, chatId) {
  const refreshed = page.waitForResponse(response => response.url().endsWith('/api/chats'))
  await page.evaluate(({ kind, chatId }) => {
    window.globalEvents.dispatchEvent(new MessageEvent('message', {
      data: JSON.stringify({ kind, chat_id: chatId, at: Date.now() / 1000 })
    }))
  }, { kind, chatId })
  await refreshed
}

for (const group of ['All', 'Date', 'Project']) {
  test(`${group} keeps start-time order and timestamps through live activity, reload and workspace reset`, async ({ page, context }) => {
    const chats = await fixture(context)
    // Saved activity-order preferences from previous releases must not opt users
    // back into rows that jump on every streamed update.
    await context.addInitScript(key => {
      if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ chatOrderBy: 'activity' }))
    }, PREFERENCES_KEY)
    await page.goto('/chats/alpha')
    await page.getByRole('button', { name: 'Group', exact: true }).click()
    await page.getByRole('group', { name: 'Group chats by', exact: true }).getByRole('button', { name: group, exact: true }).click()
    const expected = ['User just asked', 'Agent replied', 'Agent using tools']
    await expect(titles(page)).toHaveText(expected)
    if (group === 'Project') await expect(page.locator('.side__group .truncate')).toHaveText(['Project user', 'Project reply', 'Project tools'])
    const working = chats[0]
    const row = page.locator('a[href="/chats/working"]')
    const time = await page.evaluate(async timestamp => (await import('/src/format.js')).shortTime(timestamp), working.created_at)
    await expect(row.locator('.row-item__time')).toHaveText(time)
    await expect(row.locator('.row-item__time')).toHaveAttribute('title', /^Started /)
    for (const clock of ['updated_at', 'last_message_at', 'last_user_message_at']) {
      working[clock] = Date.now() / 1000
      chats.reverse() // The API may return a different live-activity order too.
      await emit(page, 'chat.progress', working.id)
      await expect(titles(page)).toHaveText(expected)
      await expect(row.locator('.row-item__time')).toHaveText(time)
      await expect(row.getByRole('img', { name: 'Active', exact: true })).toBeVisible()
    }
    Object.assign(working, { answering: false, unread: true })
    await emit(page, 'chat.completed', working.id)
    await expect(row.getByRole('img', { name: 'Inactive', exact: true })).toBeVisible()
    await expect(row.getByLabel('Unread messages')).toBeVisible()
    await expect(titles(page)).toHaveText(expected)
    await expect(page).toHaveURL(/\/chats\/alpha$/)
    await expect(page.locator('.row-item--active .row-item__title')).toHaveText('User just asked')
    await page.reload()
    await expect(titles(page)).toHaveText(expected)
    const settings = await context.newPage()
    await settings.goto('/settings/workspace')
    await expect(settings.getByLabel('Chat order', { exact: true })).toHaveCount(0)
    await settings.getByRole('button', { name: 'Reset workspace', exact: true }).click()
    await expect(titles(page)).toHaveText(expected)
  })
}

for (const group of ['All', 'Project']) {
  test(`showing older chats preserves overall creation order in ${group}`, async ({ page, context }) => {
    const chats = await fixture(context)
    // A recent conversation can have no recent activity while an older one runs.
    chats[1].updated_at = Date.now() / 1000 - 7200
    for (const chat of chats) chat.project_ids = ['tools']
    await page.goto('/chats/alpha')
    await page.getByRole('button', { name: 'Group', exact: true }).click()
    await page.getByRole('group', { name: 'Group chats by', exact: true }).getByRole('button', { name: group, exact: true }).click()
    await page.getByRole('group', { name: 'Show chats active within', exact: true }).getByRole('button', { name: '1h', exact: true }).click()
    await expect(titles(page)).toHaveText(['User just asked', 'Agent using tools'])
    await page.getByRole('button', { name: 'Show 1 older', exact: true }).click()
    await expect(titles(page)).toHaveText(['User just asked', 'Agent replied', 'Agent using tools'])
    await page.getByRole('button', { name: 'Hide 1 older', exact: true }).click()
    await expect(titles(page)).toHaveText(['User just asked', 'Agent using tools'])
  })
}
