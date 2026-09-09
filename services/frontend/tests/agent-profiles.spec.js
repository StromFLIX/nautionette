import { test, expect } from '@playwright/test'
import { mockAgents, projectId } from './agent-fixture.js'
import { openChatConfiguration } from './helpers.js'

async function dismiss (page) {
  await page.keyboard.press('Escape')
  await expect(page.locator('.q-menu')).toHaveCount(0)
}
async function chooseEffort (page, label, effort) {
  await page.getByRole('button', { name: label, exact: true }).click()
  await page.getByRole('menuitemradio', { name: effort, exact: true }).click()
}
async function chooseAgent (page, name) {
  await page.getByRole('button', { name: 'Select agent', exact: true }).click()
  await page.getByRole('menuitemradio', { name, exact: true }).click()
}
async function selectNoTools (page, label = 'Tools') {
  await page.getByLabel(label, { exact: true }).click()
  await page.locator('.q-menu').getByRole('button', { name: 'None', exact: true }).click()
  await dismiss(page)
}
const saveDefaults = page => page.locator('.settings__save').getByRole('button', { name: 'Save', exact: true }).click()

for (const width of [1440, 320]) {
  test(`global model, reasoning, tools and projects save and apply to sidebar chats at ${width}px`, async ({ page, context }) => {
    const state = await mockAgents(context)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/settings/general')
    await page.getByLabel('Default agent set', { exact: true }).click()
    await page.locator('.q-menu').getByRole('button', { name: 'research', exact: true }).click()
    await chooseEffort(page, 'Default reasoning', 'High')
    await selectNoTools(page, 'Default tools')
    await page.getByLabel('Default projects', { exact: true }).click()
    await page.getByRole('checkbox', { name: 'owner/repository', exact: true }).check()
    await dismiss(page)
    await saveDefaults(page)
    await expect.poll(() => state.settings.default_project_ids).toEqual([projectId])
    expect(state.settings.default_tools).toEqual([])
    expect(state.settings.default_reasoning_effort).toBe('high')
    expect(state.writes[0]).not.toHaveProperty('git_authorship_mode')
    await page.reload()
    await expect(page.getByLabel('Default agent set', { exact: true })).toContainText('research')
    await expect(page.getByLabel('Default reasoning', { exact: true })).toContainText('High')
    await expect(page.getByLabel('Default tools', { exact: true })).toContainText('No tools')
    await expect(page.getByLabel('Default projects', { exact: true })).toContainText('1 project')
    expect(await page.locator('.settings__body').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.goto('/chats')
    await page.getByRole('button', { name: 'New chat', exact: true }).click()
    await expect(page).toHaveURL(/\/chats\/created-1$/)
    expect(state.created[0]).toEqual({})
    const chat = state.chats['created-1'].chat
    expect(chat.agent_set).toBe('research')
    expect(chat.reasoning_effort).toBe('high')
    expect(chat.tools).toEqual([])
    expect(chat.project_ids).toEqual([projectId])
    await openChatConfiguration(page)
    await expect(page.getByLabel('Reasoning effort', { exact: true })).toContainText('High')
  })

  test(`agents save only overrides, can become default, duplicate and restore inheritance at ${width}px`, async ({ page, context }) => {
    const state = await mockAgents(context)
    state.settings.default_reasoning_effort = 'high'
    state.settings.default_project_ids = [projectId]
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/settings/agents#agent-profiles')
    await page.getByRole('button', { name: 'New agent', exact: true }).click()
    await page.getByLabel('Agent name', { exact: true }).fill('Writer')
    await expect(page.locator('.profile-editor').getByText('Inherited', { exact: true })).toHaveCount(5)
    await expect(page.getByLabel('Reasoning', { exact: true })).toContainText('High')
    await selectNoTools(page)
    await page.getByLabel('Projects', { exact: true }).click()
    await page.getByRole('button', { name: 'Clear project selection' }).click()
    await dismiss(page)
    await expect(page.locator('.profile-editor').getByText('Inherited', { exact: true })).toHaveCount(3)
    await page.getByRole('button', { name: 'Save agent', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Edit Writer', exact: true })).toBeVisible()
    expect(state.agents[0].config).toEqual({ tools: [], project_ids: [] })
    await page.getByRole('button', { name: 'Make Writer the default agent', exact: true }).click()
    await expect.poll(() => state.settings.default_agent_id).toBe(state.agents[0].id)
    await page.reload()
    await expect(page.getByRole('button', { name: 'Make Writer the default agent', exact: true })).toBeDisabled()
    await page.getByRole('button', { name: 'Duplicate Writer', exact: true }).click()
    await expect(page.getByLabel('Agent name', { exact: true })).toHaveValue('Writer copy')
    await page.getByRole('button', { name: 'Use global tools', exact: true }).click()
    await expect(page.getByLabel('Tools', { exact: true })).toContainText('All tools')
    await page.getByRole('button', { name: 'Save agent', exact: true }).click()
    await expect.poll(() => state.agents.length).toBe(2)
    expect(state.agents[1].config).toEqual({ project_ids: [] })
    expect(state.agents[0].config.tools).toEqual([])
    expect(await page.locator('.settings__body').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.goto('/chats')
    await page.getByRole('button', { name: 'New chat', exact: true }).click()
    await expect(page).toHaveURL(/\/chats\/created-1$/)
    await openChatConfiguration(page)
    await expect(page.getByRole('button', { name: 'Select agent', exact: true })).toContainText('Writer')
    await expect(page.getByRole('button', { name: 'Reasoning effort', exact: true })).toContainText('High')
    expect(state.chats['created-1'].chat.tools).toEqual([])
    expect(state.chats['created-1'].chat.project_ids).toEqual([])
  })
}

test('welcome waits for asynchronous defaults without erasing draft text or deliberate overrides', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.settings.default_reasoning_effort = 'high'
  state.settings.default_agent_set = 'research'
  state.settings.default_tools = ['mail_search']
  state.settings.default_project_ids = [projectId]
  let release
  state.catalogGate = new Promise(resolve => { release = resolve })
  await page.goto('/chats')
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('A draft before discovery')
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeDisabled()
  await openChatConfiguration(page)
  await selectNoTools(page, 'Select tools')
  release()
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeEnabled()
  await expect(page.getByLabel('Message', { exact: true })).toHaveValue('A draft before discovery')
  await expect(page.getByLabel('Reasoning effort', { exact: true })).toContainText('High')
  await expect(page.getByLabel('Select agent set', { exact: true })).toContainText('research')
  await expect(page.getByLabel('Select projects', { exact: true })).toContainText('1 project')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => state.created.length).toBe(1)
  expect(state.created[0]).toMatchObject({ agent_id: null, model: 'test/model', agent_set: 'research', reasoning_effort: 'high', tools: [], project_ids: [projectId] })
})

test('welcome applies the selected default agent and resets reasoning only on an explicit model change', async ({ page, context }) => {
  const state = await mockAgents(context)
  const agent = state.addAgent('Research', { reasoning_effort: 'high', tools: ['mail_search'], project_ids: [projectId] })
  state.settings.default_agent_id = agent.id
  await page.goto('/chats')
  await openChatConfiguration(page)
  await expect(page.getByLabel('Select agent', { exact: true })).toContainText('Research')
  await expect(page.getByLabel('Reasoning effort', { exact: true })).toContainText('High')
  await page.getByLabel('Select model', { exact: true }).click()
  await page.getByPlaceholder('Search models').fill('Plain')
  await page.getByRole('button', { name: /Plain Images unverified/ }).click()
  await expect(page.getByLabel('Reasoning effort', { exact: true })).toContainText('Provider default')
  await expect(page.getByLabel('Reasoning effort', { exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Reapply agent defaults', exact: true }).click()
  await expect(page.getByLabel('Reasoning effort', { exact: true })).toContainText('High')
  await page.getByLabel('Message', { exact: true }).fill('Use the saved agent')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => state.created.length).toBe(1)
  expect(state.created[0]).toMatchObject({ agent_id: agent.id, model: 'test/model', reasoning_effort: 'high', tools: ['mail_search'], project_ids: [projectId] })
})

test('a failed initial catalog load is retryable and never sends guessed defaults', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.catalogFailure = true
  await page.goto('/chats')
  await page.getByLabel('Message', { exact: true }).fill('Keep this draft')
  await expect(page.getByRole('alert')).toContainText('Catalog unavailable')
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeDisabled()
  state.catalogFailure = false
  state.settings.default_tools = []
  await page.getByRole('button', { name: 'Retry defaults', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => state.created.length).toBe(1)
  expect(state.created[0].tools).toEqual([])
})

for (const mutation of ['global defaults', 'new default agent', 'deleted default agent']) {
  test(`successful ${mutation} writes reach welcome even when catalog refresh fails`, async ({ page, context }) => {
    const state = await mockAgents(context)
    if (mutation === 'deleted default agent') {
      state.settings.default_tools = []
      state.settings.default_agent_id = state.addAgent('Writer', { tools: null }).id
    }
    await page.goto(mutation === 'global defaults' ? '/settings/general' : '/settings/agents')
    if (mutation === 'global defaults') {
      await selectNoTools(page, 'Default tools')
      state.catalogFailure = true
      await saveDefaults(page)
      await expect(page.getByText('Saved', { exact: true })).toBeVisible()
    } else if (mutation === 'new default agent') {
      await page.getByRole('button', { name: 'New agent', exact: true }).click()
      await page.getByLabel('Agent name', { exact: true }).fill('Writer')
      await selectNoTools(page)
      state.catalogFailure = true
      await page.getByRole('button', { name: 'Save agent', exact: true }).click()
      await page.getByRole('button', { name: 'Make Writer the default agent', exact: true }).click()
      await expect(page.getByText('Default agent updated', { exact: true })).toBeVisible()
    } else {
      await expect(page.getByRole('button', { name: 'Delete Writer', exact: true })).toBeVisible()
      state.catalogFailure = true
      await page.getByRole('button', { name: 'Delete Writer', exact: true }).click()
      await page.getByRole('dialog').getByRole('button', { name: 'OK', exact: true }).click()
      await expect(page.getByText('Agent deleted', { exact: true })).toBeVisible()
    }
    // Router navigation keeps the current store rather than fetching a fresh app.
    await page.getByRole('link', { name: 'Chats', exact: true }).click()
    await page.getByLabel('Message', { exact: true }).fill('Use the confirmed save')
    await page.getByRole('button', { name: 'Send message', exact: true }).click()
    await expect.poll(() => state.created.length).toBe(1)
    expect(state.created[0].tools).toEqual([])
    expect(state.created[0].agent_id).toBe(mutation === 'new default agent' ? state.agents[0].id : null)
  })
}

test('a late catalog response cannot undo a confirmed defaults edit', async ({ page, context }) => {
  const state = await mockAgents(context)
  await page.goto('/settings/general')
  await selectNoTools(page, 'Default tools')
  let release
  const before = state.catalogReads
  state.catalogGate = new Promise(resolve => { release = resolve })
  state.pendingEvents.push('settings.changed')
  await expect.poll(() => state.catalogReads).toBeGreaterThan(before)
  state.catalogGate = null
  state.catalogFailure = true
  await saveDefaults(page)
  await expect(page.getByText('Saved', { exact: true })).toBeVisible()
  const staleResponse = page.waitForResponse(response => response.url().includes('/api/catalog') && response.status() === 200)
  release()
  await (await staleResponse).finished()
  await page.getByRole('link', { name: 'Chats', exact: true }).click()
  await page.getByLabel('Message', { exact: true }).fill('Keep the saved restrictions')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => state.created.length).toBe(1)
  expect(state.created[0].tools).toEqual([])
})

test('restoring model inheritance keeps an explicit effort when the model itself is unchanged', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.addAgent('Writer', { model: 'test/model', reasoning_effort: 'high' })
  await page.goto('/settings/agents')
  await page.getByRole('button', { name: 'Edit Writer', exact: true }).click()
  await page.getByRole('button', { name: 'Use global model', exact: true }).click()
  await expect(page.getByLabel('Reasoning', { exact: true })).toContainText('High')
  await page.getByRole('button', { name: 'Save agent', exact: true }).click()
  await expect.poll(() => state.agents[0].config).toEqual({ reasoning_effort: 'high' })
})

test('an unavailable agent list shows a retry, not a false empty state', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.addAgent('Writer')
  state.catalogFailure = true
  await page.goto('/settings/agents')
  await expect(page.getByRole('alert')).toContainText('Catalog unavailable')
  await expect(page.getByRole('button', { name: 'New agent', exact: true })).toBeDisabled()
  await expect(page.locator('.profiles__list')).toHaveCount(0)
  state.catalogFailure = false
  await page.getByRole('button', { name: 'Retry agents', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Edit Writer', exact: true })).toBeVisible()
})

test('settings events refresh untouched defaults but keep per-field overrides in a welcome draft', async ({ page, context }) => {
  const state = await mockAgents(context)
  await page.goto('/chats')
  await openChatConfiguration(page)
  await selectNoTools(page, 'Select tools')
  await page.getByLabel('Message', { exact: true }).fill('Keep writing')
  state.settings.default_project_ids = [projectId]
  state.settings.default_reasoning_effort = 'high'
  state.settings.default_tools = ['mail_read']
  state.pendingEvents.push('settings.changed')
  await expect(page.getByLabel('Select projects', { exact: true })).toContainText('1 project')
  await expect(page.getByLabel('Reasoning effort', { exact: true })).toContainText('High')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => state.created.length).toBe(1)
  expect(state.created[0].tools).toEqual([])
  expect(state.created[0].project_ids).toEqual([projectId])
})

test('chat agent switches are persisted before Send and do not edit a saved agent', async ({ page, context }) => {
  const state = await mockAgents(context)
  const agent = state.addAgent('Writer', { tools: [], reasoning_effort: 'high', project_ids: [projectId] })
  await page.goto('/chats/alpha')
  await openChatConfiguration(page)
  let release
  state.chatPatchGate = new Promise(resolve => { release = resolve })
  await chooseAgent(page, 'Writer')
  await expect.poll(() => state.chatWrites.length).toBe(1)
  await page.getByLabel('Message', { exact: true }).fill('Work with these defaults')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeDisabled()
  expect(state.sent).toHaveLength(0)
  release()
  await expect.poll(() => state.sent.length).toBe(1)
  expect(state.sent[0].project_ids).toEqual([projectId])
  await expect(page.getByLabel('Select agent', { exact: true })).toContainText('Writer')
  await page.getByLabel('Select tools', { exact: true }).click()
  await page.locator('.q-menu').getByRole('button', { name: 'All', exact: true }).click()
  await dismiss(page)
  await expect.poll(() => state.data.chat.tools).toBe(null)
  expect(agent.config.tools).toEqual([])
  await page.getByRole('button', { name: 'Reapply agent defaults', exact: true }).click()
  await expect.poll(() => state.data.chat.tools).toEqual([])
  await page.reload()
  await openChatConfiguration(page)
  await expect(page.getByLabel('Select agent', { exact: true })).toContainText('Writer')
  expect(state.agentWrites).toEqual([])
})

test('None stays explicit in an empty catalog and selecting every current tool does not opt into future tools', async ({ page, context }) => {
  const state = await mockAgents(context)
  const tools = [...state.catalog.tools]
  state.catalog.tools = []
  await page.goto('/settings/general')
  await selectNoTools(page, 'Default tools')
  await saveDefaults(page)
  await expect.poll(() => state.settings.default_tools).toEqual([])
  state.catalog.tools = tools
  await page.reload()
  await page.getByLabel('Default tools', { exact: true }).click()
  await page.locator('.tools__group .tools__row').click()
  await dismiss(page)
  await saveDefaults(page)
  await expect.poll(() => state.settings.default_tools).toEqual(['mail_search', 'mail_read'])
  state.catalog.tools.push({ name: 'mail_delete', server: 'mail' })
  await page.reload()
  await expect(page.getByLabel('Default tools', { exact: true })).toContainText('2/3 tools')
  await page.getByLabel('Default tools', { exact: true }).click()
  await page.locator('.q-menu').getByRole('button', { name: 'All', exact: true }).click()
  await dismiss(page)
  await saveDefaults(page)
  await expect.poll(() => state.settings.default_tools).toBe(null)
})

test('unavailable tools remain pinned and can be removed without enabling all tools', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.settings.default_tools = ['removed_tool']
  await page.goto('/settings/general')
  await page.getByLabel('Default tools', { exact: true }).click()
  await expect(page.getByText('Unavailable selections stay pinned.')).toBeVisible()
  await page.getByRole('button', { name: 'Remove unavailable tool removed_tool' }).click()
  await dismiss(page)
  await saveDefaults(page)
  await expect.poll(() => state.settings.default_tools).toEqual([])
})

test('failed agent and default edits retain input, and resetting defaults leaves agents and Git settings alone', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.agentFailure = true
  await page.goto('/settings/agents')
  await page.getByRole('button', { name: 'New agent', exact: true }).click()
  await page.getByLabel('Agent name', { exact: true }).fill('My agent')
  await selectNoTools(page)
  await page.getByRole('button', { name: 'Save agent', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Agent could not be saved')
  await expect(page.getByLabel('Agent name', { exact: true })).toHaveValue('My agent')
  await expect(page.getByLabel('Tools', { exact: true })).toContainText('No tools')
  state.agentFailure = false
  await page.getByRole('button', { name: 'Save agent', exact: true }).click()
  await expect.poll(() => state.agents.length).toBe(1)
  await page.goto('/settings/general')
  await page.getByLabel('Default agent', { exact: true }).click()
  await page.getByRole('menuitemradio', { name: 'My agent', exact: true }).click()
  state.settingsFailure = true
  await saveDefaults(page)
  await expect(page.getByText('Defaults could not be saved', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Default agent', { exact: true })).toContainText('My agent')
  state.settingsFailure = false
  await page.locator('.settings__save').getByRole('button', { name: 'Reset', exact: true }).click()
  await expect(page.getByLabel('Default agent', { exact: true })).toContainText('Global defaults')
  expect(state.settings.default_agent_id).toBe(null)
  expect(state.agents[0].config.tools).toEqual([])
  expect(state.writes.at(-1)).not.toHaveProperty('git_authorship_mode')
  expect(state.settings.git_authorship_mode).toBe('automation')
})

test('deleting the default agent preserves existing chats and returns new chats to global defaults', async ({ page, context }) => {
  const state = await mockAgents(context)
  const agent = state.addAgent('Writer', { tools: [] })
  state.settings.default_agent_id = agent.id
  Object.assign(state.data.chat, { agent_id: agent.id, agent_name: agent.name, tools: [] })
  await page.goto('/settings/agents')
  await page.getByRole('button', { name: 'Delete Writer', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'OK', exact: true }).click()
  await expect.poll(() => state.agents.length).toBe(0)
  expect(state.settings.default_agent_id).toBe(null)
  expect(state.data.chat.tools).toEqual([])
  await page.goto('/chats/alpha')
  await openChatConfiguration(page)
  await expect(page.getByLabel('Select agent', { exact: true })).toContainText('Writer')
  await expect(page.getByText('Agent removed', { exact: true })).toBeVisible()
  await chooseAgent(page, 'Global defaults')
  await expect.poll(() => state.data.chat.agent_id).toBe(null)
  expect(state.data.chat.tools).toBe(null)
})

test('new settings are searchable and deep-link to their controls', async ({ page, context }) => {
  await mockAgents(context)
  await page.goto('/settings/general')
  for (const [query, target] of [['default reasoning', 'default-reasoning'], ['default projects', 'default-projects'], ['saved agents', 'agent-profiles']]) {
    await page.getByRole('searchbox', { name: 'Search settings' }).fill(query)
    await page.getByRole('region', { name: 'Settings search results' }).getByRole('link').first().click()
    await expect(page.locator(`#${target}`)).toBeVisible()
    await expect(page).toHaveURL(new RegExp(`#${target}$`))
  }
})

test('sidebar chat creation reports stale defaults instead of silently dropping project access', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.chatFailure = true
  await page.goto('/chats')
  await page.getByRole('button', { name: 'New chat', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('no longer ready')
  await expect(page.getByRole('link', { name: 'Review defaults', exact: true })).toBeVisible()
  expect(state.created).toEqual([])
})

for (const [theme, width] of [['orbit', 1440], ['nebula', 320], ['daylight', 1440], ['sand', 320]]) {
  test(`${theme} keeps the agent editor and pickers usable at ${width}px`, async ({ page, context }) => {
    const state = await mockAgents(context)
    state.addAgent('A deliberately long agent name for focused project work', { tools: [] })
    await page.addInitScript(theme => localStorage.setItem('nautionette.preferences.v1', JSON.stringify({ version: 1, theme })), theme)
    await page.setViewportSize({ width, height: 900 })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto('/settings/agents')
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await page.getByRole('button', { name: /Edit A deliberately long agent/ }).click()
    await expect(page.getByRole('button', { name: 'Save agent', exact: true })).toBeVisible()
    expect(await page.locator('.settings__body').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/nautionette-agents-${theme}.png`, fullPage: true })
    await page.getByLabel('Tools', { exact: true }).click()
    const box = await page.locator('.q-menu').boundingBox()
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(width + 1)
    await dismiss(page)
    expect(errors).toEqual([])
  })
}
