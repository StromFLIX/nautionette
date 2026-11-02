import { test, expect } from '@playwright/test'

function sample () {
  return {
    available: true, coverage: 'recorded', agent_calls: 3, model_requests: 7,
    tool_calls: 5, mcp_calls: 4, direct_mcp_calls: 2, usage_reports: 7,
    tokens: { input: 1000, output: 200, cacheRead: 30, cacheWrite: 4, total: 1234 }, usage_complete: true,
    agents: [
      { agent_set: 'research', model: 'test/research-model', calls: 2, completed: 1, failed: 1 },
      { agent_set: 'default', model: 'test/writer-model', calls: 1, completed: 1 },
    ],
    tools: [
      { name: 'github_search_repositories', source: 'workflow', calls: 2, completed: 1, failed: 1 },
      { name: 'github_search_repositories', source: 'agent_mcp', calls: 2, completed: 2 },
      { name: 'bash', source: 'local', calls: 1, completed: 1 },
    ],
  }
}

async function mockApi (page, statistics, { live = false, expired = false } = {}) {
  const state = { reads: 0, statistics }
  const run = { workflow_id: 'wf', workflow: 'digest', status: live ? 'running' : 'completed', input: {}, result: {}, created_at: 1767225600, trigger: 'manual' }
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    let data = {}
    if (path === '/api/system') data = { components: [], agent_sets: [] }
    if (path === '/api/catalog') data = { models: [], tools: [], agent_sets: [] }
    if (path === '/api/chats') data = { chats: [] }
    if (path === '/api/drafts') data = { drafts: [] }
    if (path === '/api/workflows') data = { workflows: [] }
    if (path === '/api/runs') data = { runs: [run] }
    if (path === '/api/runs/wf') {
      state.reads++
      data = { run, statistics: state.statistics, temporal: expired ? null : { status: run.status.toUpperCase() }, ...(expired ? { history_status: 'expired', note: 'Execution history expired.' } : {}) }
    }
    if (path === '/api/runs/wf/graph') data = { nodes: [], edges: [], warnings: [], status: run.status }
    return route.fulfill({ json: data })
  })
  return state
}

for (const width of [1440, 320]) {
  test(`run statistics show agent/model and named tool breakdowns at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await mockApi(page, sample(), { expired: true })
    await page.goto('/runs/wf')
    await page.getByRole('button', { name: 'Statistics', exact: true }).click()
    const panel = page.getByRole('region', { name: 'Run statistics' })
    await expect(panel.locator('.statistics-card strong')).toHaveText(['3', '5', '4', '1,234'])
    await expect(panel.getByText('test/research-model')).toBeVisible()
    await expect(panel.getByText('test/writer-model')).toBeVisible()
    await expect(panel.getByText('github_search_repositories', { exact: true })).toHaveCount(2)
    await expect(panel.getByText('MCP · direct workflow call', { exact: true })).toBeVisible()
    await expect(panel.getByText('MCP · agent call', { exact: true })).toBeVisible()
    await expect(panel.getByText('Input (uncached)', { exact: true })).toBeVisible()
    await expect(panel.getByText('Partial', { exact: true })).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(errors).toEqual([])
  })
}

test('missing token usage and old runs are not displayed as zero tokens', async ({ page }) => {
  const data = sample()
  data.tokens = null; data.usage_complete = false
  const state = await mockApi(page, data, { live: true })
  await page.clock.install()
  await page.goto('/runs/wf')
  await page.getByRole('button', { name: 'Statistics', exact: true }).click()
  await expect(page.locator('.statistics-card strong').last()).toHaveText('—')
  await expect(page.getByText('Token usage was not reported by the provider or agent image.')).toBeVisible()
  state.statistics = { available: false }
  await page.clock.fastForward(5000)
  await expect(page.getByText('Statistics were not recorded for this run.', { exact: false })).toBeVisible()
})

test('live statistics refresh and partial usage is visibly labeled', async ({ page }) => {
  const state = await mockApi(page, sample(), { live: true })
  await page.clock.install()
  await page.goto('/runs/wf')
  await page.getByRole('button', { name: 'Statistics', exact: true }).click()
  await expect(page.locator('.statistics-card strong').first()).toHaveText('3')
  state.statistics = { ...sample(), agent_calls: 4, usage_complete: false }
  await page.clock.fastForward(5000)
  await expect(page.locator('.statistics-card strong').first()).toHaveText('4')
  await expect(page.getByText('Partial', { exact: true })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'Usage is incomplete' })).toBeVisible()
})
