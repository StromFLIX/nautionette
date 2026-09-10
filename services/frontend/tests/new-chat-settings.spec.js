import { test, expect } from '@playwright/test'
import { mockAgents, projectId } from './agent-fixture.js'
import { openChatConfiguration } from './helpers.js'
import { LAST_CHAT_SETTINGS_KEY } from '../src/new-chat-settings.js'

const toggle = page => page.getByRole('button', { name: 'Chat configuration', exact: true })
const newChat = page => page.getByRole('button', { name: 'New chat', exact: true }).click()
const stored = page => page.evaluate(prefix => {
  const key = Object.keys(localStorage).find(key => key.startsWith(`${prefix}:`))
  return key ? JSON.parse(localStorage.getItem(key)) : null
}, LAST_CHAT_SETTINGS_KEY)
async function send (page, text = 'Use these settings') {
  await page.getByLabel('Message', { exact: true }).fill(text)
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
}
async function noTools (page) {
  await page.getByLabel('Select tools', { exact: true }).click()
  await page.locator('.q-menu').getByRole('button', { name: 'None', exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(page.locator('.q-menu')).toHaveCount(0)
}

test('configuration opens only manually, including legacy preferences and reused chat views', async ({ page, context }) => {
  await mockAgents(context)
  await page.addInitScript(() => localStorage.setItem('nautionette.preferences.v1', JSON.stringify({ composerExpanded: true })))
  await page.goto('/chats')
  await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false')
  await toggle(page).click()
  await expect(toggle(page)).toHaveAttribute('aria-expanded', 'true')
  await newChat(page)
  await expect(page).toHaveURL(/\/chats\/created-1$/)
  await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false')
  await toggle(page).click()
  await newChat(page) // Same ChatPane, different chat ID.
  await expect(page).toHaveURL(/\/chats\/created-2$/)
  await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false')
  await toggle(page).click()
  await page.reload()
  await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false')
  await page.getByRole('link', { name: 'Chats', exact: true }).click()
  await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false')
})

for (const entry of ['sidebar', 'welcome']) {
  test(`${entry} reuses all last-used settings across reload without carrying internet approval`, async ({ page, context }) => {
    const state = await mockAgents(context)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    const agent = state.addAgent('Writer')
    const settings = { agent_id: agent.id, model: 'test/model', agent_set: 'research', reasoning_effort: 'high',
      tools: ['mail_search'], project_ids: [projectId], packages: ['extension-revision'] }
    Object.assign(state.data.chat, settings, { agent_name: agent.name, internet_status: 'allowed' })
    await page.goto('/chats/alpha')
    await send(page)
    await expect.poll(() => state.sent.length).toBe(1)
    await expect.poll(() => stored(page)).toEqual(settings)
    // Merely looking at another chat, or changing global defaults, is not "last used".
    state.chats.other = { chat: { ...state.data.chat, id: 'other', model: 'test/plain', tools: null }, messages: [], active_turn: null }
    await page.goto('/chats/other')
    await expect(page.getByLabel('Select model', { exact: true })).toContainText('Plain')
    state.settings.default_model = 'test/plain'
    state.settings.default_tools = []
    state.settings.default_agent_id = state.addAgent('New default').id
    await page.goto('/chats')
    await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false')
    if (entry === 'sidebar') await newChat(page)
    else {
      await openChatConfiguration(page)
      await expect(page.getByLabel('Select agent', { exact: true })).toContainText('Writer')
      await expect(page.getByLabel('Reasoning effort', { exact: true })).toContainText('High')
      await expect(page.getByLabel('Select projects', { exact: true })).toContainText('1 project')
      await expect(page.getByLabel('Select extensions', { exact: true })).toContainText('1 selected')
      await send(page)
    }
    await expect.poll(() => state.created.length).toBe(1)
    expect(state.created[0]).toEqual(settings)
    expect(state.chats['created-1'].chat).not.toHaveProperty('internet_status')
    await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false')
    expect(errors).toEqual([])
  })
}

test('unsent welcome selections survive reload and are used by the sidebar', async ({ page, context }) => {
  const state = await mockAgents(context)
  await page.goto('/chats')
  await openChatConfiguration(page)
  await page.getByLabel('Reasoning effort', { exact: true }).click()
  await page.getByRole('menuitemradio', { name: 'High', exact: true }).click()
  await noTools(page)
  await expect.poll(async () => (await stored(page))?.tools).toEqual([])
  await page.reload()
  await openChatConfiguration(page)
  await expect(page.getByLabel('Reasoning effort', { exact: true })).toContainText('High')
  await expect(page.getByLabel('Select tools', { exact: true })).toContainText('0/2 tools')
  await newChat(page)
  await expect.poll(() => state.created.length).toBe(1)
  expect(state.created[0]).toMatchObject({ reasoning_effort: 'high', tools: [], project_ids: [], packages: [] })
})

for (const entry of ['sidebar', 'welcome']) {
  test(`defaults mode persists and uses current default agent settings from ${entry}`, async ({ page, context }) => {
    const state = await mockAgents(context)
    Object.assign(state.data.chat, { tools: [], reasoning_effort: 'high' })
    await page.goto('/chats/alpha')
    await send(page)
    await expect.poll(() => state.sent.length).toBe(1)
    await page.goto('/settings/workspace')
    const mode = page.getByLabel('New chat settings', { exact: true })
    await expect(mode).toHaveValue('last')
    await mode.selectOption({ label: 'Always use defaults' })
    await page.reload()
    await expect(mode).toHaveValue('defaults')
    const agent = state.addAgent('Default writer', { tools: ['mail_read'], project_ids: [projectId] })
    state.settings.default_agent_id = agent.id
    await page.goto('/chats')
    if (entry === 'sidebar') await newChat(page)
    else await send(page)
    await expect.poll(() => state.created.length).toBe(1)
    if (entry === 'sidebar') expect(state.created[0]).toEqual({})
    const created = state.chats['created-1'].chat
    expect(created).toMatchObject({ agent_id: agent.id, reasoning_effort: null, tools: ['mail_read'], project_ids: [projectId] })
    // Recording still works in defaults mode so switching back uses the latest chat.
    await page.goto('/settings/workspace')
    await mode.selectOption({ label: 'Always use last settings' })
    await page.goto('/chats')
    await send(page)
    await expect.poll(() => state.created.length).toBe(2)
    expect(state.created[1]).toMatchObject({ agent_id: agent.id, tools: ['mail_read'], project_ids: [projectId] })
  })
}

test('only confirmed configuration edits update last settings, not failed saves', async ({ page, context }) => {
  const state = await mockAgents(context)
  await page.goto('/chats/alpha')
  await openChatConfiguration(page)
  await noTools(page)
  await expect.poll(async () => (await stored(page))?.tools).toEqual([])
  state.chatPatchFailure = true
  await page.getByLabel('Reasoning effort', { exact: true }).click()
  await page.getByRole('menuitemradio', { name: 'High', exact: true }).click()
  await expect(page.getByText('Chat settings could not be saved', { exact: true })).toBeVisible()
  expect((await stored(page)).reasoning_effort).toBe(null)
  state.chatPatchFailure = false
  await newChat(page)
  await expect.poll(() => state.created.length).toBe(1)
  expect(state.created[0]).toMatchObject({ tools: [], reasoning_effort: null })
})

test('removed profiles keep last restrictions without sending an invalid agent reference', async ({ page, context }) => {
  const state = await mockAgents(context)
  const agent = state.addAgent('Writer', { tools: [] })
  Object.assign(state.data.chat, { agent_id: agent.id, agent_name: agent.name, tools: [] })
  await page.goto('/chats/alpha')
  await send(page)
  await expect.poll(() => state.sent.length).toBe(1)
  state.agents = []
  await page.goto('/chats')
  await send(page)
  await expect.poll(() => state.created.length).toBe(1)
  expect(state.created[0]).toMatchObject({ agent_id: null, tools: [] })
})

test('remembered settings wait for the catalog and explicit changes are not erased by late discovery', async ({ page, context }) => {
  const state = await mockAgents(context)
  Object.assign(state.data.chat, { reasoning_effort: 'high', tools: ['mail_search'] })
  await page.goto('/chats/alpha')
  await send(page)
  await expect.poll(() => state.sent.length).toBe(1)
  let release
  state.catalogGate = new Promise(resolve => { release = resolve })
  await page.goto('/chats')
  await page.getByLabel('Message', { exact: true }).fill('Keep my draft')
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeDisabled()
  await openChatConfiguration(page)
  await noTools(page)
  release()
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeEnabled()
  await expect(page.getByLabel('Message', { exact: true })).toHaveValue('Keep my draft')
  await expect(page.getByLabel('Reasoning effort', { exact: true })).toContainText('High')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => state.created.length).toBe(1)
  expect(state.created[0]).toMatchObject({ tools: [], reasoning_effort: 'high' })
})
