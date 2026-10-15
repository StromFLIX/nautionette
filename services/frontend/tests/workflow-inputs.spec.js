import { expect, test } from '@playwright/test'

async function mockApi (page, initialSchedule = null) {
  const state = { run: null, schedule: null }
  const workflow = {
    name: 'weekly_audit', title: 'Weekly audit', code: 'MANIFEST = {}\n',
    graph: { nodes: [], edges: [] }, runs: [], settings: {}, schedule: initialSchedule,
    manifest: { inputs: { type: 'object', properties: {
      days: { type: 'integer', minimum: 1, maximum: 14, default: 7 },
      dry_run: { type: 'boolean', default: true },
      threshold: { type: 'number' },
      label: { type: 'string' }
    } } }
  }
  await page.route('**/api/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    let data = {}
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/system') data = { components: [], agent_sets: [] }
    if (path === '/api/catalog') data = { models: [], tools: [], agent_sets: [] }
    if (path === '/api/chats') data = { chats: [] }
    if (path === '/api/drafts') data = { drafts: [] }
    if (path === '/api/runs') data = { runs: [] }
    if (path === '/api/workflows') data = { workflows: [workflow] }
    if (path === '/api/workflows/weekly_audit') data = workflow
    if (path === '/api/workflows/weekly_audit/run' && request.method() === 'POST') {
      state.run = request.postDataJSON()
      data = { workflow_id: 'audit-test' }
    }
    if (path === '/api/workflows/weekly_audit/schedule' && request.method() === 'POST') {
      state.schedule = request.postDataJSON()
      workflow.schedule = { ...state.schedule, description: 'Every Monday', next_run: null }
      data = workflow.schedule
    }
    return route.fulfill({ json: data })
  })
  await page.goto('/workflows/weekly_audit?tab=Run')
  await expect(page.getByLabel('dry_run')).toBeVisible()
  return state
}

for (const dryRun of [false, true]) {
  test(`run submits typed numbers and Boolean ${dryRun}, without converting strings`, async ({ page }) => {
    const state = await mockApi(page)
    await page.getByLabel('days', { exact: true }).fill('7')
    await page.getByLabel('dry_run').selectOption(String(dryRun))
    await page.getByLabel('threshold').fill('0.25')
    await page.getByLabel('label', { exact: true }).fill('false')
    await page.getByRole('button', { name: 'Run now' }).click()
    await expect.poll(() => state.run).toEqual({
      input: { days: 7, dry_run: dryRun, threshold: 0.25, label: 'false' }
    })
  })
}

test('untouched optional values stay omitted, rather than changing the safe dry-run default', async ({ page }) => {
  const state = await mockApi(page)
  await expect(page.getByLabel('dry_run')).toContainText('Use default (true)')
  await page.getByRole('button', { name: 'Run now' }).click()
  await expect.poll(() => state.run).toEqual({ input: {} })
})

test('schedule and trigger retain false and zero as typed values', async ({ page }) => {
  const state = await mockApi(page)
  await page.getByLabel('days', { exact: true }).fill('7')
  await page.getByLabel('dry_run').selectOption('false')
  await page.getByLabel('threshold').fill('0')
  await expect(page.locator('.trigger__code')).toContainText('"dry_run":false')
  await expect(page.locator('.trigger__code')).toContainText('"days":7')
  await page.getByLabel('Repeat').selectOption('weekly')
  await page.getByLabel('At', { exact: true }).fill('09:00')
  await page.getByLabel('Timezone').fill('Europe/Berlin')
  await page.getByRole('button', { name: 'Mon', exact: true }).click()
  await page.getByRole('button', { name: 'Save schedule' }).click()
  await expect.poll(() => state.schedule).toEqual({
    frequency: 'weekly', at: '09:00', days: ['monday'], timezone: 'Europe/Berlin',
    input: { days: 7, dry_run: false, threshold: 0 }
  })
})

test('restores scheduled typed values and allows returning a Boolean to its workflow default', async ({ page }) => {
  const state = await mockApi(page, {
    frequency: 'weekly', at: '09:00', days: ['monday'], timezone: 'Europe/Berlin',
    input: { days: 7, dry_run: false, threshold: 0 }, description: 'Every Monday'
  })
  await expect(page.getByLabel('days', { exact: true })).toHaveValue('7')
  await expect(page.getByLabel('dry_run')).toHaveValue('false')
  await expect(page.getByLabel('threshold')).toHaveValue('0')
  await page.getByLabel('days', { exact: true }).fill('8')
  await page.getByLabel('dry_run').selectOption({ label: 'Use default (true)' })
  await page.getByRole('button', { name: 'Run now' }).click()
  await expect.poll(() => state.run).toEqual({ input: { days: 8, threshold: 0 } })
})
