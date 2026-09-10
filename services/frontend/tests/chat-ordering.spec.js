import { test, expect } from '@playwright/test'
import { mockDesign } from './design-fixture.js'

const titles = page => page.locator('.side__list .row-item__title')

async function fixture (context) {
  const state = await mockDesign(context)
  const now = Date.now() / 1000
  const chats = [
    { ...state.data.chat, id: 'working', title: 'Agent using tools', project_ids: ['tools'], answering: true,
      updated_at: now - 60, last_message_at: now - 300, last_user_message_at: now - 600 },
    { ...state.data.chat, id: 'replied', title: 'Agent replied', project_ids: ['reply'], answering: false,
      updated_at: now - 120, last_message_at: now - 120, last_user_message_at: now - 500 },
    { ...state.data.chat, title: 'User just asked', project_ids: ['user'], answering: false,
      updated_at: now - 180, last_message_at: now - 180, last_user_message_at: now - 180 }
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
  await page.evaluate(({ kind, chatId }) => {
    window.globalEvents.dispatchEvent(new MessageEvent('message', {
      data: JSON.stringify({ kind, chat_id: chatId, at: Date.now() / 1000 })
    }))
  }, { kind, chatId })
}

for (const grouped of [false, true]) {
  test(`chat order settings apply live, persist and reset${grouped ? ' with project grouping' : ''}`, async ({ page, context }) => {
    const chats = await fixture(context)
    await page.goto('/chats/alpha')
    if (grouped) {
      await page.getByRole('button', { name: 'Group', exact: true }).click()
      await page.getByRole('group', { name: 'Group chats by', exact: true }).getByRole('button', { name: 'Project', exact: true }).click()
    }
    await expect(titles(page)).toHaveText(['Agent using tools', 'Agent replied', 'User just asked'])
    const settings = await context.newPage()
    await settings.goto('/settings/workspace')
    const order = settings.getByLabel('Chat order', { exact: true })
    await expect(order).toHaveValue('activity')
    await order.selectOption('messages')
    await expect(titles(page)).toHaveText(['Agent replied', 'User just asked', 'Agent using tools'])
    if (grouped) await expect(page.locator('.side__group .truncate')).toHaveText(['Project reply', 'Project user', 'Project tools'])
    const time = await page.evaluate(async timestamp => (await import('/src/format.js')).shortTime(timestamp), chats[0].last_message_at)
    await expect(page.locator('.row-item', { hasText: 'Agent using tools' }).locator('.row-item__time')).toHaveText(time)
    await order.selectOption('user')
    await expect(titles(page)).toHaveText(['User just asked', 'Agent replied', 'Agent using tools'])
    await page.reload()
    await expect(titles(page)).toHaveText(['User just asked', 'Agent replied', 'Agent using tools'])
    await settings.reload()
    await expect(settings.getByLabel('Chat order', { exact: true })).toHaveValue('user')
    await settings.getByRole('button', { name: 'Reset workspace', exact: true }).click()
    await expect(settings.getByLabel('Chat order', { exact: true })).toHaveValue('activity')
    await expect(titles(page)).toHaveText(['Agent using tools', 'Agent replied', 'User just asked'])
  })
}

test('agent progress in an unopened chat refreshes ordering without moving the selected chat', async ({ page, context }) => {
  const chats = await fixture(context)
  const working = chats[0]
  working.updated_at = working.last_message_at
  await page.goto('/chats/alpha')
  await expect(titles(page)).toHaveText(['Agent replied', 'User just asked', 'Agent using tools'])
  working.updated_at = Date.now() / 1000
  await emit(page, 'chat.progress', working.id)
  await expect(titles(page)).toHaveText(['Agent using tools', 'Agent replied', 'User just asked'])
  await expect(page).toHaveURL(/\/chats\/alpha$/)
  await expect(page.locator('.row-item--active .row-item__title')).toHaveText('User just asked')

  const settings = await context.newPage()
  await settings.goto('/settings/workspace')
  await settings.getByLabel('Chat order', { exact: true }).selectOption('messages')
  await expect(titles(page)).toHaveText(['Agent replied', 'User just asked', 'Agent using tools'])
  working.updated_at += 60 // Tools do not reorder the message-only view.
  const refreshed = page.waitForResponse(response => response.url().endsWith('/api/chats'))
  await emit(page, 'chat.progress', working.id)
  await refreshed
  await expect(titles(page)).toHaveText(['Agent replied', 'User just asked', 'Agent using tools'])
  working.last_message_at = working.updated_at // Streamed prose does, before the turn completes.
  await emit(page, 'chat.progress', working.id)
  await expect(titles(page)).toHaveText(['Agent using tools', 'Agent replied', 'User just asked'])
  await expect(page.locator('.row-item', { hasText: 'Agent using tools' })).toContainText('In progress')
})
