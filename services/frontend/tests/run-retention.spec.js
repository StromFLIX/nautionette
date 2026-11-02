import { test, expect } from '@playwright/test'

const note = 'Detailed execution history has expired or was removed from Temporal. Saved run status, input, and output are still available.'
async function mockApi (page, status = 'completed') {
  const state = { requests: 0 }
  const run = { workflow_id: 'old-run', workflow: 'digest', status, input: { days: 3 }, result: { summary: 'Saved output' }, created_at: 1767225600, trigger: 'schedule' }
  const graph = { mode: 'execution', status, history_status: 'expired', nodes: [{ id: 'workflow', label: 'digest', kind: 'workflow', status }], edges: [], warnings: [note] }
  const workflow = { name: 'digest', title: 'Digest', graph, code: '', manifest: { inputs: {} }, runs: [run], settings: {} }
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    let data = {}
    if (path === '/api/system') data = { components: [], agent_sets: [] }
    if (path === '/api/catalog') data = { models: [], tools: [], agent_sets: [] }
    if (path === '/api/chats') data = { chats: [] }
    if (path === '/api/drafts') data = { drafts: [] }
    if (path === '/api/workflows') data = { workflows: [workflow] }
    if (path === '/api/workflows/digest') data = workflow
    if (path === '/api/runs') data = { runs: [run] }
    if (path === '/api/runs/old-run') data = { run, temporal: null, history_status: 'expired', note }
    if (path === '/api/runs/old-run/graph') { state.requests++; data = graph }
    return route.fulfill({ json: data })
  })
  return state
}

for (const width of [1440, 320]) {
  test(`expired run shows saved results and a non-retrying history notice at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    const state = await mockApi(page)
    await page.goto('/runs/old-run')
    await expect(page.getByRole('status').filter({ hasText: 'Detailed execution history' })).toContainText('Detailed execution history has expired')
    await expect(page.getByText('Saved output', { exact: false })).toBeVisible()
    await expect(page.locator('.pane-head .chip')).toHaveText('completed')
    await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Flow', exact: true }).click()
    await expect(page.getByText('Execution history expired', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Retry', exact: true })).toHaveCount(0)
    await expect(page.locator('.flow-node')).toHaveCount(0)
    await page.clock.install()
    const requests = state.requests
    await page.clock.fastForward(15000)
    expect(state.requests).toBe(requests)
    await page.getByRole('link', { name: 'View saved run details' }).click()
    await expect(page.getByText('Saved output', { exact: false })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(errors).toEqual([])
  })
}

test('expired workflow flow keeps its source picker and links to saved run details', async ({ page }) => {
  const state = await mockApi(page, 'running') // Even a stale saved status must not poll forever.
  await page.goto('/workflows/digest')
  await page.getByRole('combobox', { name: 'Flow source' }).selectOption('old-run')
  await expect(page.getByText('Execution history expired', { exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Flow source' })).toBeVisible()
  await page.clock.install()
  const requests = state.requests
  await page.clock.fastForward(15000)
  expect(state.requests).toBe(requests)
  await page.getByRole('link', { name: 'View saved run details' }).click()
  await expect(page).toHaveURL(/\/runs\/old-run$/)
  await expect(page.getByText('Saved output', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toHaveCount(0)
})
