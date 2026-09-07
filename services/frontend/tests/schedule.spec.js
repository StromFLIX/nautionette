import { expect, test } from '@playwright/test'

const nextRun = '2026-09-08T05:30:00+00:00'

async function mockApi (page, initialSchedule = null) {
  const state = { schedule: initialSchedule, request: null }
  const workflow = () => ({
    name: 'morning_digest',
    title: 'Morning digest',
    description: 'Summarize updates.',
    code: 'MANIFEST = {}\n',
    graph: { nodes: [], edges: [] },
    manifest: {
      inputs: {
        type: 'object',
        properties: { topic: { type: 'string', description: 'Digest topic' } }
      }
    },
    runs: [],
    settings: {},
    schedule: state.schedule
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    let data = {}
    if (path === '/api/events') {
      return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    }
    if (path === '/api/system') data = { components: [], agent_sets: [] }
    if (path === '/api/catalog') data = { models: [], tools: [], agent_sets: [] }
    if (path === '/api/chats') data = { chats: [] }
    if (path === '/api/drafts') data = { drafts: [] }
    if (path === '/api/runs') data = { runs: [] }
    if (path === '/api/workflows') data = { workflows: [workflow()] }
    if (path === '/api/workflows/morning_digest') data = workflow()
    if (path === '/api/workflows/morning_digest/schedule' && request.method() === 'POST') {
      state.request = request.postDataJSON()
      const scheduledNextRun = '2026-09-08T05:15:00+00:00'
      state.schedule = {
        ...state.request,
        description: 'Tue, Thu at 07:15',
        paused: false,
        next_run: scheduledNextRun,
        next_runs: [scheduledNextRun]
      }
      data = state.schedule
    }
    if (path === '/api/workflows/morning_digest/schedule' && request.method() === 'DELETE') {
      state.schedule = null
      data = { ok: true }
    }
    return route.fulfill({ json: data })
  })
  return state
}

test('shows the scheduler-owned next run on desktop', async ({ page }) => {
  await mockApi(page, {
    frequency: 'daily',
    at: '07:30',
    timezone: 'Europe/Berlin',
    input: { topic: 'engineering' },
    description: 'Every day at 07:30',
    paused: false,
    next_run: nextRun,
    next_runs: [nextRun]
  })
  await page.goto('/workflows/morning_digest')
  await page.getByRole('button', { name: 'Run', exact: true }).click()

  await expect(page.locator('.schedule-current')).toContainText('Every day at 07:30')
  await expect(page.locator('.schedule-current')).toContainText('Europe/Berlin')
  await expect(page.locator('.schedule-current')).toContainText('Next run')
  await expect(page.locator('.schedule-current')).not.toContainText('Calculating')
  await expect(page.getByLabel('topic')).toHaveValue('engineering')
  await page.screenshot({ path: '/tmp/nautionette-schedule-desktop.png', fullPage: true })
})

test('creates a timezone-aware schedule without a hidden default on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 })
  const state = await mockApi(page)
  await page.goto('/workflows/morning_digest')
  await page.getByRole('button', { name: 'Run', exact: true }).click()

  await expect(page.getByLabel('At', { exact: true })).toHaveValue('')
  await expect(page.getByRole('button', { name: 'Save schedule' })).toBeDisabled()
  await page.getByLabel('Repeat').selectOption('weekly')
  await page.getByLabel('At', { exact: true }).fill('07:15')
  await page.getByLabel('Timezone').fill('Europe/Berlin')
  await page.getByRole('button', { name: 'Tue', exact: true }).click()
  await page.getByRole('button', { name: 'Thu', exact: true }).click()
  await page.getByRole('button', { name: 'Save schedule' }).click()

  await expect.poll(() => state.request).toEqual({
    frequency: 'weekly',
    at: '07:15',
    days: ['tuesday', 'thursday'],
    timezone: 'Europe/Berlin',
    input: {}
  })
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toHaveClass(/tab--active/)
  await expect(page.locator('.schedule-current')).toContainText('Tue, Thu at 07:15')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '/tmp/nautionette-schedule-mobile.png', fullPage: true })
})

test('requires an explicit replace action for an advanced existing schedule', async ({ page }) => {
  await mockApi(page, {
    frequency: 'custom',
    timezone: 'UTC',
    description: 'Custom schedule',
    paused: false,
    next_run: nextRun,
    next_runs: [nextRun]
  })
  await page.goto('/workflows/morning_digest')
  await page.getByRole('button', { name: 'Run', exact: true }).click()

  await expect(page.getByRole('button', { name: 'Replace schedule' })).toBeVisible()
  await expect(page.getByLabel('Repeat')).toHaveCount(0)
  await page.getByRole('button', { name: 'Replace schedule' }).click()
  await expect(page.getByLabel('Repeat')).toHaveValue('daily')
  await expect(page.getByLabel('At', { exact: true })).toHaveValue('')
  await expect(page.getByRole('button', { name: 'Update schedule' })).toBeDisabled()
})