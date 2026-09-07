import { test, expect } from '@playwright/test'

async function mockChats (context, state) {
  await context.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/system') {
      if (state.systemOffline) return route.abort('internetdisconnected')
      return route.fulfill({ json: { components: [], agent_sets: [] } })
    }
    if (path === '/api/catalog') return route.fulfill({ json: { models: [], tools: [], agent_sets: [], default_model: 'test/model' } })
    if (path === '/api/chats') return route.fulfill({ json: { chats: Object.values(state.chats).map((data) => data.chat) } })
    if (path === '/api/workflows') return route.fulfill({ json: { workflows: [] } })
    if (path === '/api/drafts') return route.fulfill({ json: { drafts: [] } })
    if (path === '/api/runs') return route.fulfill({ json: { runs: [] } })
    const match = path.match(/^\/api\/chats\/([^/]+)(?:\/(stream|messages))?$/)
    if (match) {
      const [, chatId, action] = match
      const data = state.chats[chatId]
      if (action === 'stream') {
        return route.fulfill({ contentType: 'text/event-stream', body: `retry: 100\ndata: ${JSON.stringify({ type: 'snapshot', ...data })}\n\n` })
      }
      if (action === 'messages' && method === 'POST') {
        const payload = route.request().postDataJSON()
        state.attempts.push(payload.message_id)
        if (state.offline) return route.abort('internetdisconnected')
        if (state.reject) return route.fulfill({ status: 422, json: { detail: 'Message rejected' } })
        let message = data.messages.find((item) => item.id === payload.message_id)
        if (!message) {
          message = { id: payload.message_id, chat_id: chatId, role: 'user', content: payload.text, meta: {}, created_at: Date.now() / 1000 }
          data.messages.push(message)
          data.active_turn = { id: payload.message_id, steps: [{ kind: 'text', text: `Working on ${payload.text}` }], status: '' }
        }
        return route.fulfill({ status: 202, json: { message, turn_id: payload.message_id } })
      }
      return route.fulfill({ json: data })
    }
    return route.fulfill({ json: {} })
  })
}

function initial () {
  return {
    attempts: [], offline: false, reject: false,
    chats: Object.fromEntries(['alpha', 'beta'].map((id) => [id, {
      chat: { id, title: id, agent_set: 'default', model: 'test/model', tools: null, updated_at: Date.now() / 1000 },
      messages: [], active_turn: null
    }]))
  }
}

test('web and mobile attach to the same live answer and switch between concurrent chats', async ({ browser }) => {
  const state = initial()
  const web = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const mobile = await browser.newContext({ viewport: { width: 320, height: 568 } })
  try {
    await mockChats(web, state)
    await mockChats(mobile, state)
    const desktop = await web.newPage()
    const android = await mobile.newPage()
    const errors = []
    desktop.on('pageerror', (error) => errors.push(error.message))
    android.on('pageerror', (error) => errors.push(error.message))
    await desktop.goto('/chats/alpha')
    await desktop.locator('textarea').fill('release notes')
    await desktop.locator('.composer__send').click()
    await expect(desktop.locator('.msg--assistant')).toContainText('Working on release notes')
    await android.goto('/chats/alpha')
    await expect(android.locator('.msg--user')).toContainText('release notes')
    await expect(android.locator('.msg--assistant')).toContainText('Working on release notes')
    await desktop.goto('/chats/beta')
    await desktop.locator('textarea').fill('another task')
    await desktop.locator('.composer__send').click()
    await expect(desktop.locator('.msg--assistant')).toContainText('Working on another task')
    state.chats.alpha.active_turn.steps[0].text = 'Still working while beta runs'
    await expect(android.locator('.msg--assistant')).toContainText('Still working while beta runs')
    state.chats.alpha.messages.push({ id: 'answer-alpha', role: 'assistant', content: 'Finished alpha', meta: {}, created_at: Date.now() / 1000 })
    state.chats.alpha.active_turn = null
    await expect(android.locator('.msg--assistant')).toContainText('Finished alpha')
    await expect(android.locator('textarea')).toBeEnabled()
    await android.reload()
    await expect(android.locator('.msg--assistant')).toContainText('Finished alpha')
    await desktop.screenshot({ path: '/tmp/nautionette-chat-desktop.png' })
    await android.screenshot({ path: '/tmp/nautionette-chat-mobile.png' })
    expect(await android.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(errors).toEqual([])
  } finally {
    await web.close()
    await mobile.close()
  }
})

test('offline messages show Sending, survive reload, and retry with the original ID', async ({ page, context }) => {
  const state = initial()
  state.offline = true
  await page.setViewportSize({ width: 320, height: 568 })
  await mockChats(context, state)
  await page.goto('/chats/alpha')
  await page.locator('textarea').fill('send after reconnect')
  await page.locator('.composer__send').click()
  await expect(page.getByRole('status')).toContainText('Sending')
  await expect.poll(() => state.attempts.length).toBeGreaterThan(0)
  const messageId = state.attempts[0]
  await page.screenshot({ path: '/tmp/nautionette-chat-sending-mobile.png' })
  const status = await page.getByRole('status').boundingBox()
  expect(status.x).toBeGreaterThanOrEqual(0)
  expect(status.x + status.width).toBeLessThanOrEqual(320)
  await page.reload()
  await expect(page.locator('.msg--user')).toContainText('send after reconnect')
  await expect(page.getByRole('status')).toContainText('Sending')
  state.offline = false
  await expect(page.locator('.msg--assistant')).toContainText('Working on send after reconnect', { timeout: 10000 })
  await expect(page.locator('.msg--user')).toHaveCount(1)
  await expect(page.getByLabel('Sent', { exact: true })).toBeVisible()
  expect(new Set(state.attempts)).toEqual(new Set([messageId]))
  await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter((key) => key.startsWith('nautionette.outbox.')).length)).toBe(0)
  await expect(page.getByLabel('Sent', { exact: true })).toHaveCount(0, { timeout: 5000 })
})

test('rejected messages expose retry and discard controls', async ({ page, context }) => {
  const state = initial()
  state.reject = true
  await mockChats(context, state)
  await page.goto('/chats/alpha')
  await page.locator('textarea').fill('retry me')
  await page.locator('.composer__send').click()
  await expect(page.getByRole('status')).toContainText('Not sent')
  await expect(page.getByRole('button', { name: 'Discard message' })).toBeVisible()
  state.reject = false
  await page.getByRole('button', { name: 'Retry message' }).click()
  await expect(page.locator('.msg--assistant')).toContainText('Working on retry me')
  expect(new Set(state.attempts).size).toBe(1)
})

test('a transient network error does not force a connection dialog', async ({ page, context }) => {
  const state = initial()
  state.systemOffline = true
  await mockChats(context, state)
  await page.goto('/chats/alpha')
  await expect(page.locator('textarea')).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.locator('textarea').fill('still connected')
  await page.locator('.composer__send').click()
  await expect(page.locator('.msg--assistant')).toContainText('Working on still connected')
})