import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'
import { LAST_CHAT_SETTINGS_KEY } from '../src/new-chat-settings.js'

const message = page => page.getByRole('textbox', { name: 'Message', exact: true })
const send = page => page.getByRole('button', { name: 'Send message', exact: true })
const newChat = page => page.getByRole('button', { name: 'New chat', exact: true }).click()
const chatRow = (page, id) => page.locator(`#shell-sidebar a[href="/chats/${id}"]`)
const image = { name: 'draft.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/b1sAAAAASUVORK5CYII=', 'base64') }

function deferred () {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

async function pauseCreations (context) {
  const requests = []
  await context.route('**/api/chats', async route => {
    if (route.request().method() !== 'POST') return route.fallback()
    const gate = deferred()
    requests.push(gate)
    await gate.promise
    return route.fallback()
  })
  return requests
}

for (const width of [1440, 390]) {
  test(`new chat is editable before creation finishes and keeps the same editor at ${width}px`, async ({ page, context }) => {
    const state = await mockAgents(context)
    const requests = await pauseCreations(context)
    const errors = []
    const temporaryRequests = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('request', request => {
      if (new URL(request.url()).pathname.startsWith('/api/chats/new')) temporaryRequests.push(request.url())
    })
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats')
    await expect(chatRow(page, 'alpha')).toBeVisible()
    await newChat(page)
    await expect(page).toHaveURL(/\/chats\/new$/)
    await expect(message(page)).toBeEditable()
    await expect(message(page)).toBeFocused()
    await expect(page.getByRole('status')).toContainText('Creating chat')
    await expect.poll(() => requests.length).toBe(1)
    const input = await message(page).elementHandle()
    await message(page).fill('A draft written\nbefore creation finishes')
    await page.locator('input[type=file]').setInputFiles(image)
    await expect(page.locator('.composer img')).toHaveCount(1)
    await expect(send(page)).toBeDisabled()
    await message(page).press('Enter') // No message may be sent to the temporary route.
    await message(page).evaluate(el => el.setSelectionRange(2, 9))
    const height = await message(page).evaluate(el => el.style.height)
    expect(state.sent).toEqual([])
    expect(state.created).toEqual([])
    if (width > 900) await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeDisabled()

    requests[0].resolve()
    await expect(page).toHaveURL(/\/chats\/created-1$/)
    expect(await input.evaluate(el => el.isConnected)).toBe(true)
    await expect(message(page)).toHaveValue('A draft written\nbefore creation finishes')
    await expect(message(page)).toBeFocused()
    expect(await message(page).evaluate(el => [el.selectionStart, el.selectionEnd, el.style.height])).toEqual([2, 9, height])
    await expect(page.locator('.composer img')).toHaveCount(1)
    await expect(send(page)).toBeEnabled()
    expect(state.created).toHaveLength(1)
    expect(temporaryRequests).toEqual([])
    expect(errors).toEqual([])

    await page.getByRole('button', { name: 'Remove draft.png', exact: true }).click()
    await send(page).click()
    await expect.poll(() => state.sent.length).toBe(1)
    expect(state.sent[0].text).toBe('A draft written\nbefore creation finishes')
    expect(await page.evaluate(() => window.history.state.back)).toBe('/chats')
    await page.goBack()
    await expect(page).toHaveURL(/\/chats$/) // Back must not recreate the temporary chat.
    expect(requests).toHaveLength(1)
  })
}

test('catalog discovery for remembered settings does not block typing or use an invalid agent', async ({ page, context }) => {
  const state = await mockAgents(context)
  const catalog = deferred()
  state.catalogGate = catalog.promise
  const requests = await pauseCreations(context)
  const settings = { agent_id: 'removed-agent', model: 'test/model', agent_set: 'research', reasoning_effort: 'high', tools: [], project_ids: [], packages: [] }
  await page.addInitScript(({ key, settings }) => {
    localStorage.setItem(`${key}:${encodeURIComponent(location.origin)}`, JSON.stringify(settings))
  }, { key: LAST_CHAT_SETTINGS_KEY, settings })
  await page.goto('/chats')
  await newChat(page)
  await expect(message(page)).toBeFocused()
  await message(page).fill('Typing while defaults are loading')
  await expect(send(page)).toBeDisabled()
  expect(requests).toHaveLength(0)
  catalog.resolve()
  await expect.poll(() => requests.length).toBe(1)
  await expect(message(page)).toHaveValue('Typing while defaults are loading')
  requests[0].resolve()
  await expect(page).toHaveURL(/\/chats\/created-1$/)
  await expect(message(page)).toHaveValue('Typing while defaults are loading')
  expect(state.created[0]).toEqual({ ...settings, agent_id: null })
})

test('a direct draft route tolerates initial discovery being superseded by a live refresh', async ({ page, context }) => {
  const state = await mockAgents(context)
  const catalog = deferred()
  state.catalogGate = catalog.promise
  state.pendingEvents.push('connected')
  const settings = { agent_id: null, model: 'test/model', agent_set: 'research', tools: [], project_ids: [] }
  await page.addInitScript(({ key, settings }) => {
    localStorage.setItem(`${key}:${encodeURIComponent(location.origin)}`, JSON.stringify(settings))
  }, { key: LAST_CHAT_SETTINGS_KEY, settings })
  await page.goto('/chats/new')
  await message(page).fill('Keep typing during initial discovery')
  await expect.poll(() => state.catalogReads).toBeGreaterThanOrEqual(2)
  catalog.resolve()
  await expect(page).toHaveURL(/\/chats\/created-1$/)
  await expect(message(page)).toHaveValue('Keep typing during initial discovery')
  await expect(send(page)).toBeEnabled()
  expect(state.created).toHaveLength(1)
  expect(state.created[0]).toMatchObject(settings)
})

test('failed creation keeps text and images editable and retry creates only one chat', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.chatFailure = true
  const requests = await pauseCreations(context)
  await page.goto('/chats')
  await newChat(page)
  await message(page).fill('Keep this draft')
  await page.locator('input[type=file]').setInputFiles(image)
  await expect.poll(() => requests.length).toBe(1)
  requests[0].resolve()
  await expect(page.getByRole('alert')).toContainText('Could not create chat')
  await expect(page.getByRole('link', { name: 'Review defaults', exact: true })).toBeVisible()
  await expect(message(page)).toBeEditable()
  await expect(message(page)).toHaveValue('Keep this draft')
  await expect(page.locator('.composer img')).toHaveCount(1)
  await expect(send(page)).toBeDisabled()
  await message(page).fill('Keep this edited draft')
  state.chatFailure = false
  await page.getByRole('button', { name: 'Retry creation', exact: true }).click()
  await expect(message(page)).toBeFocused()
  await expect(page.getByRole('button', { name: 'Retry creation', exact: true })).toHaveCount(0)
  await expect.poll(() => requests.length).toBe(2)
  await message(page).press('End')
  await message(page).pressSequentially(' during retry')
  requests[1].resolve()
  await expect(page).toHaveURL(/\/chats\/created-1$/)
  await expect(message(page)).toHaveValue('Keep this edited draft during retry')
  await expect(page.locator('.composer img')).toHaveCount(1)
  await expect(send(page)).toBeEnabled()
  expect(state.created).toHaveLength(1)
  expect(state.sent).toEqual([])
})

test('catalog failure is retryable without losing the new-chat draft', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.catalogFailure = true
  const settings = { agent_id: null, model: 'test/model', agent_set: 'default', tools: null, project_ids: [] }
  await page.addInitScript(({ key, settings }) => {
    localStorage.setItem(`${key}:${encodeURIComponent(location.origin)}`, JSON.stringify(settings))
  }, { key: LAST_CHAT_SETTINGS_KEY, settings })
  await page.goto('/chats')
  await newChat(page)
  await message(page).fill('Still here after failed discovery')
  await expect(page.getByRole('alert')).toContainText('Catalog unavailable')
  expect(state.created).toEqual([])
  state.catalogFailure = false
  await page.getByRole('button', { name: 'Retry creation', exact: true }).click()
  await expect(page).toHaveURL(/\/chats\/created-1$/)
  await expect(message(page)).toHaveValue('Still here after failed discovery')
  await expect(send(page)).toBeEnabled()
})

for (const entry of ['sidebar', 'welcome']) {
  test(`${entry} creation and first send do not await a slow or failing chat-list refresh`, async ({ page, context }) => {
    const state = await mockAgents(context)
    await page.goto('/chats')
    await expect(chatRow(page, 'alpha')).toBeVisible()
    const list = deferred()
    await context.route('**/api/chats', async route => {
      if (route.request().method() !== 'GET') return route.fallback()
      await list.promise
      return route.fulfill({ status: 503, json: { detail: 'Chat list unavailable' } })
    })
    if (entry === 'sidebar') await newChat(page)
    await message(page).fill('Send without waiting for the list')
    await send(page).click()
    await expect(page).toHaveURL(/\/chats\/created-1$/)
    await expect.poll(() => state.sent.length).toBe(1)
    await expect(chatRow(page, 'created-1')).toBeVisible()
    expect(state.created).toHaveLength(1)
    list.resolve()
    await expect(page.getByRole('alert')).toContainText('Chat list unavailable')
    await expect(page).toHaveURL(/\/chats\/created-1$/)
    await expect(page.getByRole('button', { name: 'Retry creation', exact: true })).toHaveCount(0)
  })
}

for (const entry of ['sidebar', 'welcome']) {
  test(`a late ${entry} creation cannot navigate away from or overwrite another chat`, async ({ page, context }) => {
    const state = await mockAgents(context)
    const requests = await pauseCreations(context)
    await page.goto('/chats')
    if (entry === 'sidebar') await newChat(page)
    else {
      await message(page).fill('Do not send this to another chat')
      await send(page).click()
    }
    await expect.poll(() => requests.length).toBe(1)
    await chatRow(page, 'alpha').click()
    await message(page).fill('Keep the existing chat draft')
    requests[0].resolve()
    await expect(chatRow(page, 'created-1')).toBeVisible()
    await expect(page).toHaveURL(/\/chats\/alpha$/)
    await expect(message(page)).toHaveValue('Keep the existing chat draft')
    expect(state.sent).toEqual([])
  })
}

test('a superseded creation does not claim a newer pending draft', async ({ page, context }) => {
  await mockAgents(context)
  const requests = await pauseCreations(context)
  await page.goto('/chats')
  await newChat(page)
  await expect.poll(() => requests.length).toBe(1)
  await chatRow(page, 'alpha').click()
  await newChat(page)
  await expect.poll(() => requests.length).toBe(2)
  await message(page).fill('This belongs to the second new chat')
  requests[0].resolve()
  await expect(chatRow(page, 'created-1')).toBeVisible()
  await expect(page).toHaveURL(/\/chats\/new$/)
  await expect(message(page)).toHaveValue('This belongs to the second new chat')
  await expect(send(page)).toBeDisabled()
  requests[1].resolve()
  await expect(page).toHaveURL(/\/chats\/created-2$/)
  await expect(message(page)).toHaveValue('This belongs to the second new chat')
  await expect(send(page)).toBeEnabled()
})

test('leaving the chat view invalidates its pending creation', async ({ page, context }) => {
  await mockAgents(context)
  const requests = await pauseCreations(context)
  await page.goto('/chats')
  await newChat(page)
  await expect.poll(() => requests.length).toBe(1)
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Settings', exact: true }).click()
  requests[0].resolve()
  // Returning to the list after the response must not resurrect its old draft.
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Chats', exact: true }).click()
  await expect(chatRow(page, 'created-1')).toBeVisible()
  await expect(page).toHaveURL(/\/chats$/)
  await expect(message(page)).toHaveValue('')
})
