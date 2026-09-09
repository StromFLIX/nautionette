import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'

async function mockPackages (context) {
  const state = await mockAgents(context)
  const agent = state.addAgent('Reviewer', { tools: [] })
  const installations = [], revisions = {}, writes = []
  let sequence = 1
  const id = () => (sequence++).toString(16).padStart(32, '0')
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
        const item = { id: id(), source: body.source, allow_scripts: body.allow_scripts, status: 'ready',
          metadata: { resolved: body.source, resources: { prompts: ['prompts'] } }, error: '' }
        installations.push(item); return reply(item, 202)
      }
      return reply({ installations })
    }
    if (url.pathname.endsWith('/revisions') && method === 'POST') {
      writes.push(body)
      if (state.configurationFailure) return reply({ detail: 'Unsupported configuration path' }, 422)
      const revision = { ...body, id: id(), filters: body.filters || {}, configuration: Object.fromEntries(
        Object.entries(body.configuration || { env: {}, files: {} }).map(([key, values]) => [key, Object.fromEntries(Object.keys(values).map(name => [name, null]))])) }
      revisions[revision.id] = revision
      return reply(revision, 201)
    }
    return reply(revisions[url.pathname.split('/').at(-1)])
  })
  return { state, agent, installations, revisions, writes }
}

for (const width of [1440, 320]) {
  test(`shared search installs and configures a pinned per-agent package at ${width}px`, async ({ page, context }) => {
    const { state, agent, writes } = await mockPackages(context)
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/settings/general')
    await page.getByRole('searchbox', { name: 'Search settings', exact: true }).fill('review')
    await page.getByRole('button', { name: 'Choose', exact: true }).click()
    await expect(page).toHaveURL(/\/settings\/packages\?source=/)
    await page.getByLabel('Agent', { exact: true }).selectOption(agent.id)
    await expect(page.getByLabel('Install from npm or public GitHub')).toHaveValue('npm:pi-test-prompts@1.2.3')
    await page.getByRole('button', { name: 'Download', exact: true }).click()
    await page.getByRole('button', { name: 'Add to agent', exact: true }).click()
    await page.getByText('Configuration & resources', { exact: true }).click()
    await page.getByLabel('extensions', { exact: true }).uncheck()
    await page.getByLabel('Managed configuration (JSON, write-only values)').fill(JSON.stringify({ env: { SERVICE_API_KEY: 'test-secret' }, files: { 'agent/service.json': '{"region":"west"}' } }))
    await page.getByRole('button', { name: 'Use configuration revision' }).click()
    await expect(page.getByText('Configuration & resources', { exact: true })).toBeVisible()
    await page.getByText('Configuration & resources', { exact: true }).click()
    await expect(page.getByLabel('Managed configuration (JSON, write-only values)')).not.toHaveValue(/test-secret/)
    await page.getByRole('button', { name: 'Save agent packages', exact: true }).click()
    await expect.poll(() => state.agents[0].config.packages?.length).toBe(1)
    expect(state.agents[0].config.tools).toEqual([])
    expect(writes[0]).toEqual({ source: 'npm:pi-test-prompts@1.2.3', allow_scripts: false })
    expect(writes.at(-1).filters.extensions).toEqual([])
    const pinned = state.agents[0].config.packages[0]
    await page.reload()
    await page.getByLabel('Agent', { exact: true }).selectOption(agent.id)
    await expect(page.getByText(`Revision ${pinned.slice(0, 8)}`, { exact: false })).toBeVisible()
    expect(await page.locator('.settings__body').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    await page.getByRole('button', { name: 'Remove from agent' }).click()
    await page.getByRole('button', { name: 'Save agent packages' }).click()
    await expect.poll(() => state.agents[0].config.packages).toEqual([])
  })
}

test('search outages are retryable without discarding the settings form', async ({ page, context }) => {
  const { state } = await mockPackages(context)
  state.searchFailure = true
  await page.goto('/settings/general')
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('pi')
  await expect(page.getByRole('alert')).toContainText('npm package search is unavailable')
  state.searchFailure = false
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Choose', exact: true })).toBeVisible()
})
