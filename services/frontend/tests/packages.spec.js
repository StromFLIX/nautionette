import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'

async function mockPackages (context) {
  const state = await mockAgents(context)
  const installations = [], revisions = {}, writes = []
  let sequence = 1
  const id = () => (sequence++).toString(16).padStart(32, '0')
  function revision (body) {
    const item = { ...body, id: id(), filters: body.filters || {}, configuration: Object.fromEntries(
      Object.entries(body.configuration || { env: {}, files: {} }).map(([key, values]) => [key, Object.fromEntries(Object.keys(values).map(name => [name, null]))])) }
    revisions[item.id] = item
    return item
  }
  function installed (source = 'npm:pi-test-prompts@1.2.3', status = 'ready') {
    const item = { id: id(), source, allow_scripts: false, status, metadata: { resolved: source, resources: { prompts: ['prompts'] } }, error: '' }
    if (status === 'ready') item.default_revision_id = revision({ installation_id: item.id }).id
    installations.push(item)
    return item
  }
  await context.route('**/api/pi-packages/**', async route => {
    const url = new URL(route.request().url()), method = route.request().method()
    const body = method === 'GET' ? null : route.request().postDataJSON()
    const reply = (json, status = 200) => route.fulfill({ json, status })
    if (url.pathname.endsWith('/search')) {
      if (state.searchFailure) return reply({ detail: 'npm package search is unavailable' }, 502)
      return reply({ packages: [{ name: 'pi-test-prompts', version: '1.2.3', description: 'Review prompts' }], next_offset: null })
    }
    if (url.pathname.endsWith('/installations')) {
      if (method === 'POST') {
        writes.push(body)
        if (state.installFailure) return reply({ detail: 'Two installations already running' }, 409)
        const item = installed(body.source, state.installStatus || 'ready')
        item.allow_scripts = body.allow_scripts
        return reply(item, 202)
      }
      if (state.libraryFailure) return reply({ detail: 'Library unavailable' }, 503)
      return reply({ installations })
    }
    if (url.pathname.endsWith('/configuration')) {
      writes.push(body)
      if (state.configurationFailure) return reply({ detail: 'Unsupported configuration path' }, 422)
      const item = installations.find(item => item.id === url.pathname.split('/').at(-2))
      const saved = revision({ ...body, installation_id: item.id })
      item.default_revision_id = saved.id
      return reply(saved)
    }
    if (url.pathname.endsWith('/revisions') && method === 'POST') { writes.push(body); return reply(revision(body), 201) }
    const selected = revisions[url.pathname.split('/').at(-1)]
    return selected ? reply(selected) : reply({ detail: 'Revision unavailable' }, 422)
  })
  return { state, installations, revisions, writes, installed, revision }
}

for (const width of [1440, 320]) {
  test(`library installation needs no agent; selection is separate at ${width}px`, async ({ page, context }) => {
    const { state, writes, installations } = await mockPackages(context)
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/settings/packages')
    await expect(page.getByRole('heading', { name: 'Extension library', exact: true })).toBeVisible()
    await expect(page.getByLabel('Agent', { exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Find extensions', exact: true }).click()
    await page.getByRole('button', { name: 'Install', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Installed packages' })).toContainText('npm:pi-test-prompts@1.2.3')
    expect(state.agents).toEqual([])
    expect(state.agentWrites).toEqual([])
    await page.getByText('Configuration & resources', { exact: true }).click()
    await page.getByLabel('extensions', { exact: true }).uncheck()
    await page.getByRole('button', { name: 'Add variable', exact: true }).click()
    await page.getByLabel('variable 1 name').fill('SERVICE_API_KEY')
    await page.getByLabel('variable 1 value').fill('test-secret')
    await page.getByRole('button', { name: 'Add file', exact: true }).click()
    await page.getByLabel('file 1 name').fill('agent/service.json')
    await page.getByLabel('file 1 content').fill('{"region":"west"}')
    await page.getByRole('button', { name: 'Save configuration', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('Saved for future selections')
    await expect(page.getByLabel('variable 1 value')).toHaveValue('')
    expect(writes[0]).toEqual({ source: 'npm:pi-test-prompts@1.2.3', allow_scripts: false })
    expect(writes.at(-1).filters.extensions).toEqual([])
    expect(await page.locator('.settings__body').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    const pinned = installations[0].default_revision_id
    await page.goto('/settings/agents')
    await page.getByRole('button', { name: 'New agent', exact: true }).click()
    await page.getByLabel('Agent name').fill('Reviewer')
    await expect(page.getByRole('button', { name: 'Install', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Select agent extensions' }).click()
    await page.getByRole('checkbox', { name: /npm:pi-test-prompts/ }).check()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Save agent', exact: true }).click()
    await expect.poll(() => state.agents[0]?.config.packages).toEqual([pinned])
    await page.getByRole('button', { name: 'Edit Reviewer', exact: true }).click()
    await page.getByRole('button', { name: 'Select agent extensions' }).click()
    await page.getByRole('checkbox', { name: /npm:pi-test-prompts/ }).uncheck()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Save agent', exact: true }).click()
    await expect.poll(() => state.agents[0].config.packages).toEqual([])
    expect(installations).toHaveLength(1)
    const gap = await page.evaluate(() => document.getElementById('agent-sets').getBoundingClientRect().top - document.getElementById('agent-profiles').getBoundingClientRect().bottom)
    expect(gap).toBeGreaterThanOrEqual(20)
  })

  test(`chat extension picker selects and deselects without changing an agent at ${width}px`, async ({ page, context }) => {
    const { state, installed, revision } = await mockPackages(context)
    const item = installed(), pinned = item.default_revision_id
    installed('npm:broken@1.0.0', 'failed')
    const agent = state.addAgent('Reviewer', { packages: [pinned] })
    state.data.chat.packages = [pinned]
    // Library defaults changed after the chat was created. Toggling must keep the chat's revision.
    item.default_revision_id = revision({ installation_id: item.id, filters: { extensions: [] } }).id
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/chats/alpha')
    if (!await page.getByRole('button', { name: 'Select extensions', exact: true }).isVisible()) await page.getByRole('button', { name: 'Chat configuration', exact: true }).click()
    await page.getByRole('button', { name: 'Select extensions', exact: true }).click()
    await expect(page.getByRole('checkbox', { name: /broken/ })).toHaveCount(0)
    await expect(page.getByRole('checkbox', { name: /pi-test-prompts/ })).toBeChecked()
    await page.getByRole('checkbox', { name: /pi-test-prompts/ }).uncheck()
    await expect.poll(() => state.chatWrites.at(-1)).toEqual({ packages: [] })
    await page.getByRole('checkbox', { name: /pi-test-prompts/ }).check()
    await expect.poll(() => state.chatWrites.at(-1)).toEqual({ packages: [pinned] })
    expect(agent.config.packages).toEqual([pinned])
    await page.keyboard.press('Escape')
    await page.reload()
    await page.getByRole('button', { name: 'Select extensions', exact: true }).click()
    await expect(page.getByRole('checkbox', { name: /pi-test-prompts/ })).toBeChecked()
    await page.getByRole('button', { name: 'Use library configuration', exact: true }).click()
    await expect.poll(() => state.chatWrites.at(-1)).toEqual({ packages: [item.default_revision_id] })
    expect(agent.config.packages).toEqual([pinned])
  })
}

test('chat extension changes wait for persistence and a failed save keeps the previous selection', async ({ page, context }) => {
  const { state, installed } = await mockPackages(context)
  const item = installed()
  state.data.chat.packages = [item.default_revision_id]
  let release
  const gate = new Promise(resolve => { release = resolve })
  await page.route('**/api/chats/alpha', async route => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    await gate
    return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Selection save unavailable' }) })
  })
  await page.goto('/chats/alpha')
  if (!await page.getByRole('button', { name: 'Select extensions', exact: true }).isVisible()) await page.getByRole('button', { name: 'Chat configuration', exact: true }).click()
  await page.getByRole('button', { name: 'Select extensions', exact: true }).click()
  const choice = page.getByRole('checkbox', { name: /pi-test-prompts/ })
  await choice.uncheck()
  await expect(choice).toBeDisabled()
  release()
  await expect(page.getByText('Selection save unavailable', { exact: true })).toBeVisible()
  await expect(choice).toBeEnabled()
  await expect(choice).toBeChecked()
})

test('new chat sends the selected extension revisions without creating an agent', async ({ page, context }) => {
  const { state, installed } = await mockPackages(context)
  const item = installed()
  await page.goto('/chats')
  if (!await page.getByRole('button', { name: 'Select extensions', exact: true }).isVisible()) await page.getByRole('button', { name: 'Chat configuration', exact: true }).click()
  await page.getByRole('button', { name: 'Select extensions', exact: true }).click()
  await page.getByRole('checkbox', { name: /pi-test-prompts/ }).check()
  await page.keyboard.press('Escape')
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Review this')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect.poll(() => state.created[0]?.packages).toEqual([item.default_revision_id])
  expect(state.agents).toHaveLength(0)
})

test('shared search links to a source installation, with retryable registry outages', async ({ page, context }) => {
  const { state, writes } = await mockPackages(context)
  state.searchFailure = true
  await page.goto('/settings/general')
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('pi')
  await expect(page.getByRole('alert')).toContainText('npm package search is unavailable')
  state.searchFailure = false
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await page.getByRole('button', { name: 'View package', exact: true }).click()
  await expect(page).toHaveURL(/\/settings\/packages\?source=/)
  await expect(page.getByLabel('Package source', { exact: true })).toHaveValue('npm:pi-test-prompts@1.2.3')
  await page.getByRole('button', { name: 'Install source', exact: true }).click()
  await expect.poll(() => writes.length).toBe(1)
})

test('failed configuration saves keep the draft and selection; retry clears secrets', async ({ page, context }) => {
  const { state, installed, installations } = await mockPackages(context)
  const item = installed(), pinned = item.default_revision_id
  const agent = state.addAgent('Reviewer', { packages: [pinned] })
  await page.goto('/settings/packages')
  await page.getByText('Configuration & resources', { exact: true }).click()
  await page.getByRole('button', { name: 'Add variable', exact: true }).click()
  await page.getByLabel('variable 1 name').fill('KEY')
  await page.getByLabel('variable 1 value').fill('secret')
  state.configurationFailure = true
  await page.getByRole('button', { name: 'Save configuration', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Unsupported configuration path')
  await expect(page.getByLabel('variable 1 value')).toHaveValue('secret')
  expect(installations[0].default_revision_id).toBe(pinned)
  state.configurationFailure = false
  await page.getByRole('button', { name: 'Save configuration', exact: true }).click()
  await expect(page.getByLabel('variable 1 value')).toHaveValue('')
  expect(agent.config.packages).toEqual([pinned])
})

test('in-progress and failed installations stay visible and retry is independent of agents', async ({ page, context }) => {
  const { state, installed, installations } = await mockPackages(context)
  const item = installed('npm:broken@1.0.0', 'failed')
  item.error = 'Installation failed'
  installed('npm:pending@1.0.0', 'installing')
  await page.goto('/settings/packages')
  await expect(page.getByText('Installing…', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Retry installation', exact: true }).click()
  await expect.poll(() => installations.length).toBe(3)
  expect(state.agents).toHaveLength(0)
})
