import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'
import { openChatConfiguration, closeChatConfiguration } from './helpers.js'

const portrait = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64')
async function fixture (context) {
  const state = await mockAgents(context)
  const research = state.addAgent('Ada', { tools: ['mail_search'], system_prompt: 'Think like a researcher.' })
  research.image = `data:image/png;base64,${portrait.toString('base64')}`
  const writer = state.addAgent('Grace', { tools: [] })
  const defaults = state.catalogSnapshot().global_chat_defaults
  const make = (id, title, agent, customized = false) => ({
    chat: { ...defaults, ...agent.config, id, title, agent_id: agent.id, agent_name: agent.name,
      agent_customized: customized, read_revision: 0, created_at: Date.now() / 1000, updated_at: Date.now() / 1000 },
    messages: [], active_turn: null
  })
  state.chats = {
    alpha: make('alpha', 'Research discussion', research),
    beta: make('beta', 'Writing discussion', writer),
    custom: make('custom', 'Customized discussion', research, true),
    switched: make('switched', 'Switched from Ada to Grace', writer),
    global: make('global', 'Unassigned discussion', { id: null, name: null, config: {} })
  }
  return { state, research, writer }
}
const rows = page => page.locator('.chat-list-item')
const select = (page, name) => page.getByRole('button', { name: `Chats with ${name}`, exact: true }).click()
const all = page => page.getByRole('button', { name: 'Show all chats', exact: true })
const back = page => page.viewportSize().width > 900
  ? page.getByRole('link', { name: 'Chats', exact: true }).click()
  : page.getByRole('button', { name: 'Back to chats' }).click()

for (const width of [1440, 390, 320]) {
  test(`All is the pinned first agent; scoped lists, search, new chats and back work at ${width}px`, async ({ page, context }) => {
    const { state, research, writer } = await fixture(context)
    state.settings.default_agent_id = writer.id
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats')
    await expect(rows(page)).toHaveCount(5)
    await expect(all(page)).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByRole('navigation', { name: 'Chats by agent' }).getByRole('button').first()).toHaveText(/All/)
    await expect(page.getByRole('button', { name: 'Chats with Ada' }).locator('img')).toBeVisible()
    await page.getByRole('button', { name: 'New chat', exact: true }).click()
    await expect(page).toHaveURL(/\/chats\/created-1$/)
    await back(page)
    await select(page, 'Ada')
    await expect(rows(page)).toHaveCount(1)
    await expect(rows(page)).toContainText('Research discussion')
    await page.getByRole('textbox', { name: 'Search chats' }).fill('Customized')
    await expect(rows(page)).toHaveCount(0)
    await page.getByRole('button', { name: 'Clear search' }).click()
    await page.reload()
    await expect(page.getByRole('button', { name: 'Chats with Ada' })).toHaveAttribute('aria-pressed', 'true')
    await expect(rows(page)).toHaveCount(1)
    await page.getByRole('button', { name: 'New chat', exact: true }).click()
    await expect(page).toHaveURL(/\/chats\/created-2\?agent=agent-1$/)
    expect(state.created[1]).toEqual({ agent_id: research.id })
    expect(state.chats['created-2'].chat.system_prompt).toBe('Think like a researcher.')
    await back(page)
    await expect(rows(page)).toHaveCount(2)
    await all(page).click()
    await expect(rows(page)).toHaveCount(7)
    await select(page, 'Grace')
    await expect(rows(page)).toHaveCount(3)
    await expect(rows(page).filter({ hasText: 'Switched from Ada to Grace' })).toHaveCount(1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `/workspace/agent-sidebar-${width}.png`, fullPage: true })
  })

  test(`customizing leaves the agent view, stays All-only after undo, and reselecting moves it at ${width}px`, async ({ page, context }) => {
    const { state, research, writer } = await fixture(context)
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`/chats/alpha?agent=${research.id}`)
    await openChatConfiguration(page)
    await page.getByRole('button', { name: 'Select tools', exact: true }).click()
    await page.locator('.q-menu').getByRole('button', { name: 'None', exact: true }).click()
    await page.keyboard.press('Escape')
    await expect(page).toHaveURL(/\/chats\/alpha$/)
    expect(state.chats.alpha.chat.agent_customized).toBe(true)
    // A settings edit can leave the agent scope without closing the in-place panel.
    await expect(page.getByRole('region', { name: 'Chat preferences' })).toBeVisible()
    await page.getByRole('button', { name: 'Reapply agent defaults', exact: true }).click()
    await expect.poll(() => state.chats.alpha.chat.agent_customized).toBe(false)
    await page.getByRole('button', { name: 'Select agent', exact: true }).click()
    await page.getByRole('menuitemradio', { name: writer.name, exact: true }).click()
    await expect.poll(() => state.chats.alpha.chat.agent_id).toBe(writer.id)
    await closeChatConfiguration(page)
    await back(page)
    await select(page, research.name)
    await expect(rows(page)).toHaveCount(0)
    await select(page, writer.name)
    await expect(rows(page)).toHaveCount(3)
    await all(page).click()
    await expect(rows(page)).toHaveCount(5)
  })

  test(`agent portraits and system prompts can be selected, edited, duplicated and cleared at ${width}px`, async ({ page, context }) => {
    const { state } = await fixture(context)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/settings/agents#agent-profiles')
    await page.getByRole('button', { name: 'New agent', exact: true }).click()
    await page.getByLabel('Agent name', { exact: true }).fill('Lin')
    await page.getByLabel('Agent image', { exact: false }).setInputFiles({ name: 'portrait.png', mimeType: 'image/png', buffer: portrait })
    await page.getByLabel('System prompt', { exact: false }).fill('You are Lin. Be practical and friendly.')
    await page.getByRole('button', { name: 'Save agent', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Edit Lin', exact: true })).toBeVisible()
    expect(state.agents.at(-1).image).toContain('data:image/png;base64,')
    expect(state.agents.at(-1).config.system_prompt).toBe('You are Lin. Be practical and friendly.')
    await page.getByRole('button', { name: 'Duplicate Lin', exact: true }).click()
    await expect(page.getByLabel('System prompt', { exact: false })).toHaveValue('You are Lin. Be practical and friendly.')
    await page.getByRole('button', { name: 'Remove image', exact: true }).click()
    await page.getByLabel('System prompt', { exact: false }).fill('')
    await page.getByRole('button', { name: 'Save agent', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Edit Lin copy', exact: true })).toBeVisible()
    expect(state.agents.at(-1).image).toBe('')
    expect(state.agents.at(-1).config).not.toHaveProperty('system_prompt')
    expect(await page.locator('.settings__body').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
  })
}

test('welcome scope overrides remembered settings; customizing its unsent draft returns to All without losing text', async ({ page, context }) => {
  const { state, research } = await fixture(context)
  await page.goto(`/chats?agent=${research.id}`)
  await page.getByLabel('Message', { exact: true }).fill('Keep this draft')
  await openChatConfiguration(page)
  await expect(page.getByRole('button', { name: 'Select agent', exact: true })).toContainText(research.name)
  await page.getByRole('button', { name: 'Select tools', exact: true }).click()
  await page.locator('.q-menu').getByRole('button', { name: 'None', exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/chats$/)
  await closeChatConfiguration(page)
  await expect(page.getByLabel('Message', { exact: true })).toHaveValue('Keep this draft')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => state.created.length).toBe(1)
  expect(state.created[0]).toMatchObject({ agent_id: research.id, agent_customized: true, tools: [], system_prompt: 'Think like a researcher.' })
  expect(state.chats['created-1'].chat.agent_customized).toBe(true)
})

test('many agents scroll independently while All stays pinned on a narrow landscape phone', async ({ page, context }) => {
  const { state } = await fixture(context)
  for (let i = 0; i < 30; i++) state.addAgent(`Person ${i}`)
  await page.setViewportSize({ width: 740, height: 360 })
  await page.goto('/chats')
  await page.getByRole('button', { name: 'Chats with Person 29', exact: true }).click()
  await expect(all(page)).toBeInViewport()
  await expect(page.getByRole('button', { name: 'New chat', exact: true })).toBeInViewport()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('deleted agents fall back to All; offline discovery does not discard a valid scope', async ({ page, context }) => {
  const { state, research } = await fixture(context)
  state.catalogFailure = true
  await page.goto(`/chats?agent=${research.id}`)
  await expect(page).toHaveURL(/\?agent=agent-1$/)
  state.catalogFailure = false
  state.pendingEvents.push('agent.profile.changed')
  await expect(page.getByRole('button', { name: 'Chats with Ada' })).toBeVisible()
  state.agents = state.agents.filter(agent => agent.id !== research.id)
  state.pendingEvents.push('agent.profile.changed')
  await expect(page).toHaveURL(/\/chats$/)
  await expect(all(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(rows(page)).toHaveCount(5)
})

test('scoped welcome resolves current server settings rather than copying a stale catalog', async ({ page, context }) => {
  const { state, research } = await fixture(context)
  await page.goto(`/chats?agent=${research.id}`)
  await page.getByLabel('Message', { exact: true }).fill('Use current Ada')
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeEnabled()
  research.config = { tools: [], system_prompt: 'An updated persona.' }
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(page).toHaveURL(/\/chats\/created-1\?agent=agent-1$/)
  expect(state.created[0]).toEqual({ agent_id: research.id })
  expect(state.chats['created-1'].chat).toMatchObject({ agent_customized: false, tools: [], system_prompt: 'An updated persona.' })
})

test('switching agent views during creation preserves the latest view when the old response arrives', async ({ page, context }) => {
  const { state, research, writer } = await fixture(context)
  let release, entered
  const gate = new Promise(resolve => { release = resolve })
  const requested = new Promise(resolve => { entered = resolve })
  await context.route('**/api/chats', async route => {
    if (route.request().method() === 'POST') { entered(); await gate }
    await route.fallback()
  })
  await page.goto(`/chats?agent=${research.id}`)
  await page.getByLabel('Message', { exact: true }).fill('Keep this draft')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await requested
  await select(page, writer.name)
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeEnabled()
  const response = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/chats')
  release()
  await (await response).finished()
  await expect.poll(() => state.created.length).toBe(1)
  await expect(page).toHaveURL(/\/chats\?agent=agent-2$/)
  await expect(page.getByLabel('Message', { exact: true })).toHaveValue('Keep this draft')
  expect(state.sent).toEqual([])
})

test('a rejected configuration change stays in its agent view', async ({ page, context }) => {
  const { state, research } = await fixture(context)
  state.chatPatchFailure = true
  await page.goto(`/chats/alpha?agent=${research.id}`)
  await openChatConfiguration(page)
  await page.getByRole('button', { name: 'Select tools', exact: true }).click()
  await page.locator('.q-menu').getByRole('button', { name: 'None', exact: true }).click()
  await page.keyboard.press('Escape')
  await expect(page.getByText('Chat settings could not be saved', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/chats\/alpha\?agent=agent-1$/)
  expect(state.chats.alpha.chat.agent_customized).toBe(false)
})
