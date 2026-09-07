import { test, expect } from '@playwright/test'

function refreshReadState (data) {
  const lastRead = data.messages.findIndex((message) => message.id === data.chat.last_read_message_id)
  data.chat.unread = Boolean(data.chat.marked_unread || data.messages.some((message, index) => index > lastRead && message.role === 'assistant'))
  data.chat.answering = Boolean(data.active_turn)
  return data
}

async function mockChats (context, state) {
  await context.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (state.disconnected) return route.abort('internetdisconnected')
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/system') {
      if (state.systemOffline) return route.abort('internetdisconnected')
      return route.fulfill({ json: { components: [], agent_sets: [] } })
    }
    if (path === '/api/catalog') return route.fulfill({ json: { models: state.models || [], tools: [], agent_sets: [], default_model: 'test/model' } })
    if (path === '/api/chats') return route.fulfill({ json: { chats: Object.values(state.chats).map((data) => refreshReadState(data).chat) } })
    if (path === '/api/workflows') return route.fulfill({ json: { workflows: [] } })
    if (path === '/api/drafts') return route.fulfill({ json: { drafts: [] } })
    if (path === '/api/runs') return route.fulfill({ json: { runs: [] } })
    const match = path.match(/^\/api\/chats\/([^/]+)(?:\/(stream|messages|internet|stop|queue\/resume|read-state))?$/)
    if (match) {
      const [, chatId, action] = match
      const data = refreshReadState(state.chats[chatId])
      if (action === 'read-state' && method === 'PATCH') {
        if (state.readFailure) return route.fulfill({ status: 503, json: { detail: 'Read status unavailable' } })
        const payload = route.request().postDataJSON()
        if ('unread' in payload) {
          data.chat.marked_unread = payload.unread
          data.chat.read_revision++
          if (!payload.unread) data.chat.last_read_message_id = data.messages.findLast((message) => message.role === 'assistant')?.id || null
        } else if (payload.revision === data.chat.read_revision) {
          if (payload.clear_manual) data.chat.marked_unread = false
          const index = data.messages.findIndex((message) => message.id === payload.message_id)
          const previous = data.messages.findIndex((message) => message.id === data.chat.last_read_message_id)
          if (index > previous) data.chat.last_read_message_id = payload.message_id
        }
        return route.fulfill({ json: refreshReadState(data).chat })
      }
      if (action === 'stop' && method === 'POST') {
        const payload = route.request().postDataJSON()
        state.stops.push({ chatId, ...payload })
        if (state.stopFailure) return route.fulfill({ status: 503, json: { detail: 'Stop could not be delivered' } })
        data.messages.push({ id: 'partial', role: 'assistant', content: data.active_turn.steps[0].text, meta: { interrupted: true, error: 'Stopped by you.' } })
        data.active_turn = null
        data.chat.queue_paused = 1
        return route.fulfill({ json: data })
      }
      if (action === 'queue/resume' && method === 'POST') {
        data.chat.queue_paused = 0
        const message = data.messages.find((item) => item.meta?.queued)
        if (message) {
          message.meta.queued = false
          data.active_turn = { id: message.id, steps: [{ kind: 'text', text: `Working on ${message.content}` }], status: '' }
        }
        return route.fulfill({ json: data })
      }
      if (action === 'internet' && method === 'POST') {
        const payload = route.request().postDataJSON()
        state.decisions.push({ chatId, ...payload })
        if (state.approvalFailure) return route.fulfill({ status: 502, json: { detail: 'Could not deliver the decision; retry shortly' } })
        data.chat.internet_status = payload.allowed ? 'allowed' : 'denied'
        return route.fulfill({ json: data.chat })
      }
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
          const queued = Boolean(payload.queue && (data.active_turn || data.chat.queue_paused))
          message = { id: payload.message_id, chat_id: chatId, role: 'user', content: payload.text, meta: queued ? { queued: true } : {}, created_at: Date.now() / 1000 }
          data.messages.push(message)
          if (!queued) data.active_turn = { id: payload.message_id, steps: [{ kind: 'text', text: `Working on ${payload.text}` }], status: '' }
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
    attempts: [], decisions: [], stops: [], offline: false, reject: false,
    chats: Object.fromEntries(['alpha', 'beta'].map((id) => [id, {
      chat: { id, title: id, agent_set: 'default', model: 'test/model', tools: null, updated_at: Date.now() / 1000, read_revision: 0, marked_unread: false, last_read_message_id: null },
      messages: [], active_turn: null
    }]))
  }
}

for (const width of [1440, 320]) {
  test(`chat list distinguishes unread replies, progress and internet approval at ${width}px`, async ({ page, context }) => {
    const state = initial()
    state.chats.alpha.active_turn = { id: 'active', steps: [] }
    state.chats.beta.active_turn = { id: 'approval', steps: [] }
    state.chats.beta.chat.internet_status = 'pending'
    state.chats.beta.messages = [{ id: 'reply', role: 'assistant', content: 'Please review this', meta: {} }]
    await mockChats(context, state)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats')
    const alpha = page.locator('a[href="/chats/alpha"]')
    const beta = page.locator('a[href="/chats/beta"]')
    await expect(alpha).toContainText('In progress')
    await expect(alpha).toHaveClass(/row-item--running/)
    await expect(beta).toContainText('Internet approval needed')
    await expect(beta).not.toHaveClass(/row-item--running/)
    await expect(beta.getByLabel('Unread messages')).toBeVisible()
    await page.getByRole('button', { name: 'Options for beta' }).click()
    await page.getByRole('button', { name: 'Mark as read', exact: true }).click()
    await expect(beta.getByLabel('Unread messages')).toHaveCount(0)
    await expect(beta).toContainText('Internet approval needed')
    await page.getByRole('button', { name: 'Options for alpha' }).click()
    await page.getByRole('button', { name: 'Mark as unread', exact: true }).click()
    await expect(alpha.getByLabel('Unread messages')).toBeVisible()
    await page.reload()
    await expect(alpha.getByLabel('Unread messages')).toBeVisible()
    await page.screenshot({ path: `/tmp/nautionette-chat-states-${width}.png` })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await alpha.click()
    await expect.poll(() => state.chats.alpha.chat.unread).toBe(false)
  })

  test(`composer light follows progress and respects reduced motion at ${width}px`, async ({ page, context }) => {
    const state = initial()
    state.chats.alpha.active_turn = { id: 'active', steps: [{ kind: 'text', text: 'Working' }] }
    await mockChats(context, state)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats/alpha')
    const composer = page.locator('.composer')
    await expect(composer).toHaveClass(/composer--running/)
    const light = () => composer.evaluate((el) => {
      const style = getComputedStyle(el, '::before')
      return { animation: style.animationName, angle: style.getPropertyValue('--composer-orbit-angle'), pointerEvents: style.pointerEvents }
    })
    expect((await light()).animation).toContain('composer-light-orbit')
    expect((await light()).pointerEvents).toBe('none')
    const angle = (await light()).angle
    await expect.poll(async () => (await light()).angle).not.toBe(angle)
    await page.locator('textarea').fill('Still editable')
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await expect.poll(async () => (await light()).animation).toBe('none')
    const avatar = page.locator('a[href="/chats/alpha"] .avatar')
    expect(await avatar.evaluate((el) => getComputedStyle(el, '::after').animationName)).toBe('none')
    await page.getByRole('button', { name: 'Stop response' }).click()
    await expect(composer).not.toHaveClass(/composer--running/)
    expect(await composer.evaluate((el) => getComputedStyle(el, '::before').content)).toBe('none')
  })

  test(`running chats queue messages and stop without overlapping controls at ${width}px`, async ({ page, context }) => {
    const state = initial()
    state.chats.alpha.active_turn = { id: 'active', steps: [{ kind: 'text', text: 'Running command' }] }
    await mockChats(context, state)
    await page.setViewportSize({ width, height: width === 320 ? 568 : 1000 })
    await page.goto('/chats/alpha')
    await expect(page.getByRole('button', { name: 'Stop response' })).toBeVisible()
    await page.locator('textarea').fill('Use the smaller implementation')
    await page.getByRole('button', { name: 'Queue message', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Queued messages' })).toContainText('Use the smaller implementation')
    await expect(page.locator('.msg--assistant')).toContainText('Running command')
    await page.reload()
    await expect(page.getByRole('region', { name: 'Queued messages' })).toContainText('Use the smaller implementation')
    const stop = await page.getByRole('button', { name: 'Stop response' }).boundingBox()
    const send = await page.getByRole('button', { name: 'Queue message', exact: true }).boundingBox()
    expect(stop.x + stop.width <= send.x || send.x + send.width <= stop.x || stop.y + stop.height <= send.y || send.y + send.height <= stop.y).toBe(true)
    await page.screenshot({ path: `/tmp/nautionette-chat-queue-${width}.png` })
    state.stopFailure = true
    await page.getByRole('button', { name: 'Stop response' }).click()
    await expect(page.getByRole('alert')).toContainText('Stop could not be delivered')
    state.stopFailure = false
    await page.getByRole('button', { name: 'Stop response' }).click()
    await expect(page.getByRole('region', { name: 'Queued messages' })).toContainText('Queue paused')
    await expect(page.getByRole('button', { name: 'Stop response' })).toHaveCount(0)
    expect(state.stops).toEqual([{ chatId: 'alpha', turn_id: 'active' }, { chatId: 'alpha', turn_id: 'active' }])
    await page.getByRole('button', { name: 'Resume queued messages' }).click()
    await expect(page.locator('.thread__body')).toContainText('Working on Use the smaller implementation')
    await expect(page.getByRole('region', { name: 'Queued messages' })).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
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

test('cached chats survive offline navigation and reload while replies queue behind stale turns', async ({ page, context }) => {
  const state = initial()
  state.chats.alpha.messages = [{ id: 'alpha-answer', role: 'assistant', content: 'Saved alpha answer', meta: {} }]
  state.chats.alpha.active_turn = { id: 'active-alpha', steps: [{ kind: 'text', text: 'Last known progress' }] }
  state.chats.beta.messages = [{ id: 'beta-answer', role: 'assistant', content: 'Saved beta answer', meta: {} }]
  await mockChats(context, state)
  await page.setViewportSize({ width: 320, height: 568 })
  await page.goto('/chats/alpha')
  await expect(page.locator('.thread__body')).toContainText('Saved alpha answer')
  await expect(page.locator('.thread__body')).toContainText('Last known progress')
  await page.goto('/chats/beta')
  await expect(page.locator('.thread__body')).toContainText('Saved beta answer')
  state.disconnected = true
  await page.reload()
  await expect(page.locator('.thread__body')).toContainText('Saved beta answer')
  await page.locator('.pane-head__back').click()
  await page.locator('a[href="/chats/alpha"]').click()
  await expect(page.locator('.thread__body')).toContainText('Saved alpha answer')
  await expect(page.locator('.thread__body')).toContainText('Last known progress')
  await page.locator('textarea').fill('Continue after reconnect')
  await page.locator('.composer__send').click()
  await expect(page.getByRole('status')).toContainText('Sending')
  await page.reload()
  await expect(page.locator('.thread__body')).toContainText('Saved alpha answer')
  await expect(page.locator('.msg--user')).toContainText('Continue after reconnect')
  await page.screenshot({ path: '/tmp/nautionette-offline-chat-mobile.png' })
  state.chats.alpha.active_turn = null
  state.disconnected = false
  await page.evaluate(() => window.dispatchEvent(new Event('online')))
  await expect(page.locator('.thread__body')).toContainText('Working on Continue after reconnect', { timeout: 10000 })
  await expect(page.locator('.msg--user')).toHaveCount(1)
  state.chats.alpha.active_turn.steps[0].text = 'Fresh streamed progress'
  await expect(page.locator('.thread__body')).toContainText('Fresh streamed progress')
  expect(new Set(state.attempts).size).toBe(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('the latest 100 chats are cached without opening each conversation', async ({ page, context }) => {
  const state = initial()
  state.chats = Object.fromEntries(Array.from({ length: 105 }, (_, index) => {
    const id = `chat-${index}`
    return [id, { chat: { id, title: id, updated_at: 1000 - index }, messages: [{ id: `answer-${index}`, role: 'assistant', content: `Saved conversation ${index}`, meta: {} }], active_turn: null }]
  }))
  await mockChats(context, state)
  await page.goto('/chats')
  await expect.poll(() => page.evaluate(async () => {
    const { chatCache } = await import('/src/chat-cache.js')
    return Boolean(await chatCache.get('chat-99'))
  })).toBe(true)
  expect(await page.evaluate(async () => {
    const { chatCache } = await import('/src/chat-cache.js')
    return await chatCache.get('chat-100')
  })).toBeNull()
  state.disconnected = true
  await page.goto('/chats/chat-99')
  await expect(page.locator('.thread__body')).toContainText('Saved conversation 99')
})

test('an uncached offline chat shows a connection state and accepts queued messages', async ({ page, context }) => {
  const state = initial()
  state.disconnected = true
  await mockChats(context, state)
  await page.goto('/chats/alpha')
  await expect(page.getByRole('status')).toContainText('Waiting for connection')
  await page.locator('textarea').fill('Queue without history')
  await page.locator('.composer__send').click()
  await expect(page.locator('.msg--user')).toContainText('Queue without history')
  await page.reload()
  await expect(page.locator('.msg--user')).toContainText('Queue without history')
})

test('unavailable local history storage does not prevent chatting', async ({ page, context }) => {
  await mockChats(context, initial())
  await context.addInitScript(() => {
    Object.defineProperty(window, 'indexedDB', { value: { open () { throw new Error('Storage unavailable') } } })
  })
  await page.goto('/chats/alpha')
  await expect(page.getByRole('alert')).toContainText('Offline history could not be saved')
  await page.locator('textarea').fill('Keep chatting')
  await page.locator('.composer__send').click()
  await expect(page.locator('.msg--assistant')).toContainText('Working on Keep chatting')
})

test('context meter uses live provider tokens, persists on reload and rejects a different model', async ({ page, context }) => {
  const state = initial()
  state.models = [{ id: 'test/model', context_length: 10000 }]
  const data = state.chats.alpha
  data.messages = [{ id: 'user-1', role: 'user', content: 'short', meta: {} }]
  data.active_turn = { id: 'user-1', steps: [], context: null }
  await mockChats(context, state)
  await page.goto('/chats/alpha')
  const meter = page.locator('.composer__context')
  await expect(meter).toHaveText('Context unknown')
  const usage = { model: 'test/model', tokens: 4600, source: 'provider' }
  data.active_turn.context = usage
  await expect(meter).toHaveText('46% context')
  await expect(meter).toHaveAttribute('title', /4,600 of 10,000 tokens/)
  data.active_turn.context = { ...usage, tokens: 1000 }
  await expect(meter).toHaveText('10% context')
  data.messages.push({ id: 'answer-1', role: 'assistant', content: 'done', meta: { context: data.active_turn.context } })
  data.active_turn = null
  await page.reload()
  await expect(meter).toHaveText('10% context')
  await page.locator('textarea').fill('This draft must not count as reported usage')
  await expect(meter).toHaveText('10% context')
  data.chat.model = 'another/model'
  await expect(meter).toHaveText('Context unknown')
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

for (const width of [1440, 320]) {
  test(`internet approval survives reload and is scoped to the chat at ${width}px`, async ({ page, context }) => {
    const state = initial()
    Object.assign(state.chats.alpha.chat, {
      internet_status: 'pending', internet_reason: 'Read the latest release notes from the project website.', internet_turn_id: 'turn-alpha'
    })
    state.chats.alpha.active_turn = { id: 'turn-alpha', steps: [], status: '' }
    await page.setViewportSize({ width, height: width === 320 ? 568 : 1000 })
    await mockChats(context, state)
    await page.goto('/chats/alpha')
    const request = page.getByRole('region', { name: 'Internet access request' })
    await expect(request).toContainText('Read the latest release notes')
    await page.reload()
    await expect(request).toBeVisible()
    await page.screenshot({ path: `/tmp/nautionette-internet-${width}.png` })
    const bounds = await request.boundingBox()
    expect(bounds.x).toBeGreaterThanOrEqual(0)
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width)
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(width === 320 ? 568 : 1000)
    await page.getByRole('button', { name: 'Allow for this chat' }).click()
    await expect(request).toHaveCount(0)
    await expect(page.getByRole('img', { name: 'Internet allowed for this chat' })).toBeVisible()
    expect(state.decisions).toEqual([{ chatId: 'alpha', turn_id: 'turn-alpha', allowed: true }])
    await page.reload()
    await expect(page.getByRole('button', { name: 'Allow for this chat' })).toHaveCount(0)
    await page.goto('/chats/beta')
    await expect(page.getByRole('img', { name: 'Internet allowed for this chat' })).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}

test('marking an open chat unread keeps the reminder until it is reopened', async ({ page, context }) => {
  const state = initial()
  state.chats.alpha.messages = [{ id: 'reply', role: 'assistant', content: 'Finished work', meta: {} }]
  await mockChats(context, state)
  await page.goto('/chats/alpha')
  await expect.poll(() => state.chats.alpha.chat.last_read_message_id).toBe('reply')
  await page.getByRole('button', { name: 'Chat options', exact: true }).click()
  await page.getByRole('button', { name: 'Mark as unread', exact: true }).click()
  await expect(page).toHaveURL(/\/chats$/)
  const alpha = page.locator('a[href="/chats/alpha"]')
  await expect(alpha.getByLabel('Unread messages')).toBeVisible()
  await page.reload()
  await expect(alpha.getByLabel('Unread messages')).toBeVisible()
  await alpha.click()
  await expect(alpha.getByLabel('Unread messages')).toHaveCount(0)
  state.chats.alpha.messages.push({ id: 'next', role: 'assistant', content: 'Fresh result', meta: {} })
  await expect.poll(() => state.chats.alpha.chat.last_read_message_id).toBe('next')
})

test('hidden chats and replies below the scroll position are not acknowledged', async ({ page, context }) => {
  const state = initial()
  state.chats.alpha.messages = [{ id: 'long', role: 'assistant', content: 'A paragraph.\n\n'.repeat(100), meta: {} }]
  await mockChats(context, state)
  await page.goto('/chats/alpha')
  await expect.poll(() => state.chats.alpha.chat.last_read_message_id).toBe('long')
  await page.locator('.thread__body').evaluate((el) => { el.scrollTop = 0 })
  state.chats.alpha.messages.push({ id: 'below', role: 'assistant', content: 'New reply below', meta: {} })
  await expect(page.locator('.thread__body')).toContainText('New reply below')
  expect(state.chats.alpha.chat.last_read_message_id).toBe('long')
  await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }))
  await page.locator('.thread__body').evaluate((el) => { el.scrollTop = el.scrollHeight })
  state.chats.alpha.messages.push({ id: 'hidden', role: 'assistant', content: 'Reply while hidden', meta: {} })
  await expect(page.locator('.thread__body')).toContainText('Reply while hidden')
  expect(state.chats.alpha.chat.last_read_message_id).toBe('long')
  await page.evaluate(() => {
    delete document.visibilityState
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect.poll(() => state.chats.alpha.chat.last_read_message_id).toBe('hidden')
})

test('read-state failures are visible and do not fake success', async ({ page, context }) => {
  const state = initial()
  state.readFailure = true
  await mockChats(context, state)
  await page.goto('/chats')
  await page.getByRole('button', { name: 'Options for alpha' }).click()
  await page.getByRole('button', { name: 'Mark as unread', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Could not update read status')
  await expect(page.getByLabel('Unread messages')).toHaveCount(0)
})

test('internet denial can be retried after delivery fails', async ({ page, context }) => {
  const state = initial()
  Object.assign(state.chats.alpha.chat, {
    internet_status: 'pending', internet_reason: 'Download a package.', internet_turn_id: 'turn-alpha'
  })
  state.approvalFailure = true
  await mockChats(context, state)
  await page.goto('/chats/alpha')
  await page.getByRole('button', { name: 'Deny', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Could not deliver')
  state.approvalFailure = false
  await page.getByRole('button', { name: 'Deny', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Internet access request' })).toHaveCount(0)
  expect(state.decisions.every((decision) => decision.allowed === false)).toBe(true)
})