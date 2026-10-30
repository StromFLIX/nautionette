import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'
import { openChatConfiguration, closeChatConfiguration } from './helpers.js'
import { THEMES, resolveTheme, contrastRatio } from '../src/themes.js'

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
    await page.screenshot({ path: test.info().outputPath(`agent-sidebar-${width}.png`), fullPage: true })
  })

  test(`compact octagonal rail and direct agent image selection work at ${width}px`, async ({ page, context }) => {
    const { state, writer } = await fixture(context)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats')
    const rail = page.getByRole('navigation', { name: 'Chats by agent' })
    const agentButton = rail.getByRole('button', { name: 'Chats with Grace', exact: true })
    await expect(agentButton).toBeVisible()
    const bounds = await rail.boundingBox()
    expect(width > 900 ? bounds.width : bounds.height).toBeLessThanOrEqual(width > 900 ? 68 : 60)
    expect((await agentButton.boundingBox()).height).toBeLessThanOrEqual(48)
    await expect(agentButton.locator('.agent-avatar')).toHaveCSS('clip-path', /^polygon\(/)
    await expect(agentButton.locator('.agent-avatar')).toHaveCSS('border-radius', '0px')
    await agentButton.hover()
    await expect(page.getByRole('tooltip')).toHaveText('Grace · 0 agents running · 0 messages waiting')
    await page.mouse.move(0, 0)
    await agentButton.click({ button: 'right' })
    await page.getByRole('link', { name: 'Change image', exact: true }).click()
    await expect(page.getByLabel('Agent name', { exact: true })).toHaveValue(writer.name)
    const preview = page.getByRole('button', { name: 'Choose agent image', exact: true })
    await expect(preview).toBeVisible()
    const chooser = page.waitForEvent('filechooser')
    await preview.click()
    await (await chooser).setFiles({ name: 'grace.png', mimeType: 'image/png', buffer: portrait })
    await expect(page.getByRole('button', { name: 'Change agent image', exact: true }).locator('img')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Change agent image', exact: true }).locator('.agent-avatar')).toHaveCSS('clip-path', /^polygon\(/)
    await page.getByRole('button', { name: 'Save agent', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Edit Grace', exact: true })).toBeVisible()
    expect(state.agents.find(agent => agent.id === writer.id).image).toContain('data:image/png;base64,')
    await page.goto('/chats')
    await expect(page.getByRole('button', { name: 'Chats with Grace', exact: true }).locator('img')).toBeVisible()
    await page.reload()
    await expect(page.getByRole('button', { name: 'Chats with Grace', exact: true }).locator('img')).toBeVisible()
    await page.screenshot({ path: test.info().outputPath(`compact-agents-${width}.png`), fullPage: true })
  })

  test(`running agents and waiting messages update across scopes at ${width}px`, async ({ page, context }) => {
    const { state, research, writer } = await fixture(context)
    state.chatActivity = { running: 4, queued: 123, agents: { [research.id]: { running: 2, queued: 1 }, [writer.id]: { running: 1, queued: 120 } } }
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`/chats?agent=${research.id}`)
    const ada = page.getByRole('button', { name: 'Chats with Ada', exact: true })
    const grace = page.getByRole('button', { name: 'Chats with Grace', exact: true })
    await expect(all(page)).toHaveAccessibleDescription('4 agents running · 123 messages waiting')
    await expect(all(page).locator('.agent-activity__badge--running')).toHaveText('4')
    await expect(all(page).locator('.agent-activity__badge--queued')).toHaveText('99+')
    await expect(ada).toHaveAccessibleDescription('2 agents running · 1 message waiting')
    await expect(ada.locator('.agent-activity__badge--running')).toHaveText('2')
    await expect(ada.locator('.agent-activity__badge--queued')).toHaveText('1')
    await expect(grace).toHaveAccessibleDescription('1 agent running · 120 messages waiting')
    // These are server-wide counts, unaffected by list scope or search.
    await page.getByRole('textbox', { name: 'Search chats' }).fill('No matches')
    await expect(rows(page)).toHaveCount(0)
    await expect(all(page)).toHaveAccessibleDescription('4 agents running · 123 messages waiting')
    await page.getByRole('button', { name: 'Clear search' }).click()
    await select(page, writer.name)
    await expect(ada).toHaveAccessibleDescription('2 agents running · 1 message waiting')
    await page.screenshot({ path: test.info().outputPath(`agent-activity-${width}.png`), fullPage: true })
    // Steering consumes a queued message, but the same two agents keep running.
    state.chatActivity.agents[research.id].queued = 0
    state.chatActivity.queued = 122
    state.pendingEvents.push('chat.queue.changed')
    await expect(ada.locator('.agent-activity__badge--queued')).toHaveCount(0)
    await expect(ada.locator('.agent-activity__badge--running')).toHaveText('2')
    // Idle agents have no distracting badges; descriptions still report zero.
    state.chatActivity = { running: 0, queued: 0, agents: {} }
    state.pendingEvents.push('chat.answered')
    await expect(page.locator('.agent-activity__badge')).toHaveCount(0)
    await expect(ada).toHaveAccessibleDescription('0 agents running · 0 messages waiting')
    // A paused queue is visible even with no running agent.
    state.chatActivity = { running: 0, queued: 1, agents: { [research.id]: { running: 0, queued: 1 } } }
    state.pendingEvents.push('chat.queue.changed')
    await expect(ada).toHaveAccessibleDescription('0 agents running · 1 message waiting')
    await expect(ada.locator('.agent-activity__badge--queued')).toHaveText('1')
    await expect(ada.locator('.agent-activity__badge--running')).toHaveCount(0)
    await page.reload()
    await expect(ada).toHaveAccessibleDescription('0 agents running · 1 message waiting')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
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
    await page.getByLabel(/^Agent image/).setInputFiles({ name: 'portrait.png', mimeType: 'image/png', buffer: portrait })
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

for (const width of [1440, 320]) {
  test(`minimal activity counters follow every theme and stay inside the rail at ${width}px`, async ({ page, context }) => {
    const { state, research, writer } = await fixture(context)
    state.chatActivity = { running: 124, queued: 123, agents: { [research.id]: { running: 1, queued: 0 }, [writer.id]: { running: 123, queued: 123 } } }
    await page.setViewportSize({ width, height: 900 })
    const rgb = hex => `rgb(${[1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16)).join(', ')})`
    const verify = async tokens => {
      const rail = page.getByRole('navigation', { name: 'Chats by agent' })
      const running = all(page).locator('.agent-activity__badge--running')
      const queued = all(page).locator('.agent-activity__badge--queued')
      await expect(running).toHaveText('99+')
      await expect(queued).toHaveText('99+')
      await expect(running).toHaveCSS('background-color', rgb(tokens.accent))
      await expect(running).toHaveCSS('color', rgb(tokens['accent-text']))
      await expect(queued).toHaveCSS('background-color', rgb(tokens['surface-raised']))
      await expect(queued).toHaveCSS('color', rgb(tokens['text-muted']))
      expect(contrastRatio(tokens['accent-text'], tokens.accent)).toBeGreaterThanOrEqual(4.5)
      expect(contrastRatio(tokens['text-muted'], tokens['surface-raised'])).toBeGreaterThanOrEqual(4.5)
      await expect(rail.locator('.agent-activity .material-icons')).toHaveCount(0)
      const badges = rail.locator('.agent-activity__badge')
      for (const badge of await badges.all()) {
        await expect(badge).toHaveAttribute('aria-hidden', 'true')
        await expect(badge).toHaveCSS('border-width', '0px')
        await expect(badge).toHaveCSS('animation-name', 'none')
        const box = await badge.boundingBox()
        const button = await badge.locator('xpath=ancestor::button').boundingBox()
        expect(box.height).toBe(16)
        expect(box.x).toBeGreaterThanOrEqual(button.x)
        expect(box.y).toBeGreaterThanOrEqual(button.y)
        expect(box.x + box.width).toBeLessThanOrEqual(button.x + button.width)
        expect(box.y + box.height).toBeLessThanOrEqual(button.y + button.height)
      }
      const runningBox = await running.boundingBox(), queuedBox = await queued.boundingBox()
      expect(queuedBox.y + queuedBox.height).toBeLessThan(runningBox.y)
      await expect(all(page)).toHaveAccessibleDescription('124 agents running · 123 messages waiting')
      await expect(page.getByRole('button', { name: 'Chats with Ada', exact: true })).toHaveAccessibleDescription('1 agent running · 0 messages waiting')
    }
    for (const theme of THEMES) {
      await page.goto('/settings/appearance')
      await page.getByRole('button', { name: `${theme.name} theme`, exact: true }).click()
      await page.goto('/chats')
      await verify(resolveTheme(theme.id))
      await page.screenshot({ path: test.info().outputPath(`activity-${theme.id}-${width}.png`) })
    }
    // Custom accents use the same accessible foreground as primary buttons.
    await page.goto('/settings/appearance#token-accent')
    await page.getByLabel('Accent', { exact: true }).fill('#551166')
    await page.getByLabel('Accent', { exact: true }).press('Tab')
    await page.goto('/chats')
    await verify(resolveTheme(THEMES.at(-1).id, { accent: '#551166' }))
  })
}

test('older list responses cannot resurrect finished agents or consumed queue counts', async ({ page, context }) => {
  const { state, research } = await fixture(context)
  state.chatActivity = { running: 1, queued: 2, agents: { [research.id]: { running: 1, queued: 2 } } }
  await page.goto('/chats')
  await expect(all(page)).toHaveAccessibleDescription('1 agent running · 2 messages waiting')
  let release, entered, staleRequest
  const gate = new Promise(resolve => { release = resolve })
  const requested = new Promise(resolve => { entered = resolve })
  let first = true
  await context.route('**/api/chats', async route => {
    if (!first) return route.fallback()
    first = false
    const snapshot = { chats: Object.values(state.chats).map(data => data.chat), activity: structuredClone(state.chatActivity) }
    staleRequest = route.request()
    entered()
    await gate
    await route.fulfill({ json: snapshot })
  })
  state.pendingEvents.push('chat.progress')
  await requested
  state.chatActivity = { running: 0, queued: 0, agents: {} }
  state.pendingEvents.push('chat.answered')
  await expect(all(page)).toHaveAccessibleDescription('0 agents running · 0 messages waiting')
  const finished = page.waitForResponse(response => response.request() === staleRequest)
  release()
  await (await finished).finished()
  await expect(all(page)).toHaveAccessibleDescription('0 agents running · 0 messages waiting')
  await expect(page.locator('.agent-activity__badge')).toHaveCount(0)
})

test('offline list history does not pretend cached agents are still running', async ({ page, context }) => {
  const { state, research } = await fixture(context)
  state.chats.alpha.chat.answering = true
  state.chatActivity = { running: 1, queued: 1, agents: { [research.id]: { running: 1, queued: 1 } } }
  await page.goto('/chats')
  await expect(all(page)).toHaveAccessibleDescription('1 agent running · 1 message waiting')
  await context.route('**/api/chats', route => route.abort('failed'))
  await page.reload()
  await expect(rows(page)).toHaveCount(5)
  await expect(all(page)).toHaveAccessibleDescription('Activity unavailable')
  await expect(page.locator('.agent-activity__badge')).toHaveCount(0)
})

test('agent context menu is keyboard accessible and canceling an image change preserves the saved portrait', async ({ page, context }) => {
  const { state, research } = await fixture(context)
  const saved = research.image
  await page.goto('/chats')
  const button = page.getByRole('button', { name: 'Chats with Ada', exact: true })
  await button.focus()
  await button.press('Shift+F10')
  await page.getByRole('link', { name: 'Change image', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Change agent image', exact: true })).toBeVisible()
  await page.getByLabel(/^Agent image/).setInputFiles({ name: 'not-an-image.txt', mimeType: 'text/plain', buffer: Buffer.from('not an image') })
  await expect(page.getByRole('alert')).toContainText('Choose a PNG, JPEG or WebP')
  await expect(page.getByRole('button', { name: 'Change agent image', exact: true }).locator('img')).toHaveAttribute('src', saved)
  await page.getByRole('button', { name: 'Remove image', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Choose agent image', exact: true }).locator('img')).toHaveCount(0)
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  expect(state.agents.find(agent => agent.id === research.id).image).toBe(saved)
  await page.reload()
  await expect(page.locator('.profile-editor')).toHaveCount(0)
  await page.goto('/chats')
  await expect(page.getByRole('button', { name: 'Chats with Ada', exact: true }).locator('img')).toHaveAttribute('src', saved)
})

test('press and hold opens image management on a phone without changing the agent view', async ({ browser }) => {
  const context = await browser.newContext({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } })
  try {
    await fixture(context)
    const page = await context.newPage()
    await page.goto('/chats')
    const agent = page.getByRole('button', { name: 'Chats with Grace', exact: true })
    await expect(agent).toBeVisible()
    const box = await agent.boundingBox()
    const session = await context.newCDPSession(page)
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: box.x + box.width / 2, y: box.y + box.height / 2 }] })
    await expect(page.getByRole('link', { name: 'Change image', exact: true })).toBeVisible()
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
    await expect(page).toHaveURL(/\/chats$/)
    await page.getByRole('link', { name: 'Change image', exact: true }).tap()
    await expect(page.getByLabel('Agent name', { exact: true })).toHaveValue('Grace')
    await expect(page.getByRole('button', { name: 'Choose agent image', exact: true })).toBeInViewport()
    await page.screenshot({ path: test.info().outputPath('agent-image-picker-mobile.png'), fullPage: true })
  } finally { await context.close() }
})

test('direct agent editor links wait for discovery rather than opening a blank agent', async ({ page, context }) => {
  const { state, research } = await fixture(context)
  let release
  state.catalogGate = new Promise(resolve => { release = resolve })
  await page.goto(`/settings/agents?edit=${research.id}#profile-image`)
  await expect(page.locator('.profile-editor')).toHaveCount(0)
  release()
  await expect(page.getByLabel('Agent name', { exact: true })).toHaveValue(research.name)
  await expect(page.getByRole('button', { name: 'Change agent image', exact: true })).toBeVisible()
})

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
