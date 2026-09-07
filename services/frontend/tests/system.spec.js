import { test, expect } from '@playwright/test'

async function mockSystem (context) {
  const state = {
    fail: false,
    payload: {
      version: '0.9.0', auth_enabled: true, model: 'provider/model-with-a-long-name', model_key_present: false,
      components: [
        { name: 'temporal', status: 'ok', detail: 'temporal:7233' },
        { name: 'broker', status: 'degraded', detail: {
          status: 'degraded', docker: true, images: ['nautionette/pi-base:dev'], image_status: 'missing',
          missing_images: ['nautionette/agents/research-with-a-very-long-image-name:latest'],
          workers: { status: 'degraded', ready: 0, desired: 1, containers: [
            { name: 'worker-1', status: 'running', health: 'unhealthy', detail: JSON.stringify({ status: 'degraded', error: 'Workflow import failed\nModuleNotFoundError: custom_dependency' }) },
            { name: 'worker-2', status: 'running', health: 'healthy', detail: JSON.stringify({ status: 'ready', files: ['hello_world.py', 'url_digest.py'] }) },
            { name: 'worker-3', status: 'exited', health: null, detail: '{"status":"degraded","error":"truncated health output' }
          ], error: null },
          error: 'Image build failed: registry unavailable'
        } },
        { name: 'agentgateway', status: 'ok', detail: { status: 'degraded', code: 503 } },
        { name: 'workflow-mcp', status: 'ok', detail: { status: 'ok', workflows_dir: '/workflows', drafts_dir: '/data/drafts' } }
      ],
      agent_sets: [{ name: 'default', image: 'nautionette/default:dev', ready: true }, { name: 'research', image: 'nautionette/research:dev', ready: false }]
    }
  }
  await context.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/system') return route.fulfill({ status: state.fail ? 503 : 200, json: state.fail ? { detail: 'unavailable' } : state.payload })
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    const key = path.split('/').pop()
    return route.fulfill({ json: key === 'catalog' ? { models: [], tools: [], agent_sets: [] } : { [key]: [] } })
  })
  return state
}

for (const width of [1440, 320]) {
  test(`system health exposes nested diagnostics at ${width}px`, async ({ page, context }) => {
    await mockSystem(context)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({ width, height: width === 320 ? 568 : 1000 })
    await page.goto('/settings/system')
    await expect(page.getByRole('heading', { name: 'System health', exact: true })).toBeVisible()
    await expect(page.getByText('2 of 4 services healthy')).toBeVisible()
    await expect(page.getByText('Services need attention', { exact: true })).toBeVisible()
    await expect(page.getByText('provider/model-with-a-long-name', { exact: true })).toBeVisible()
    const broker = page.getByRole('article', { name: 'Docker broker' })
    await expect(broker.getByText('Docker reachable', { exact: true })).toBeVisible()
    await expect(broker.getByText('0', { exact: true })).toBeVisible()
    await expect(broker.getByText('nautionette/agents/research-with-a-very-long-image-name:latest', { exact: true })).toBeVisible()
    await expect(broker.getByText('Workflow import failed\nModuleNotFoundError: custom_dependency', { exact: true })).toBeVisible()
    await expect(broker.getByText('hello_world.py', { exact: true })).toBeVisible()
    await expect(broker.getByText('{"status":"degraded","error":"truncated health output', { exact: true })).toBeVisible()
    await expect(page.getByRole('article', { name: 'Agent gateway' }).getByText('Degraded', { exact: true })).toBeVisible()
    await expect(page.getByRole('article', { name: 'Workflow MCP' }).getByText('/data/drafts', { exact: true })).toBeVisible()
    await expect(page.locator('.system-health__agent').filter({ hasText: 'research' })).toContainText('Missing')
    await page.getByText('Raw system response', { exact: true }).click()
    await expect(page.locator('.system-health__raw pre')).toContainText('"model_key_present": false')
    expect(await page.locator('.system-health').evaluate(element => [...element.querySelectorAll('*')].every(child => child.getBoundingClientRect().right <= innerWidth))).toBe(true)
    await page.getByText('Raw system response', { exact: true }).click()
    await page.locator('.settings__body').evaluate(element => { element.scrollTop = 0 })
    await page.screenshot({ path: `/tmp/nautionette-system-${width}.png`, fullPage: true })
    const workerError = broker.getByText('Workflow import failed\nModuleNotFoundError: custom_dependency', { exact: true })
    await workerError.scrollIntoViewIfNeeded()
    expect(await workerError.locator('..').evaluate(element => element.getBoundingClientRect().width)).toBeGreaterThan(180)
    await page.screenshot({ path: `/tmp/nautionette-system-broker-${width}.png` })
    expect(errors).toEqual([])
  })
}

test('refresh preserves old diagnostics on failure and recovers', async ({ page, context }) => {
  const state = await mockSystem(context)
  await page.goto('/settings/system')
  const refresh = page.getByRole('button', { name: 'Refresh system health' })
  await expect(refresh).toBeEnabled()
  state.fail = true
  await refresh.click()
  await expect(page.getByRole('alert')).toContainText('Showing the last available results.')
  await expect(page.getByText('Health check unavailable', { exact: true })).toBeVisible()
  await expect(page.getByText('Image build failed: registry unavailable', { exact: true })).toBeVisible()
  state.fail = false
  state.payload.components = [{ name: 'temporal', status: 'ok', detail: 'temporal:7233' }]
  state.payload.auth_enabled = false
  state.payload.model_key_present = true
  await refresh.click()
  await expect(page.getByText('All services healthy', { exact: true })).toBeVisible()
  await expect(page.getByText('Disabled', { exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('empty health response is unknown, not healthy', async ({ page, context }) => {
  const state = await mockSystem(context)
  state.payload = { components: [], agent_sets: [] }
  await page.goto('/settings/system')
  await expect(page.getByText('Awaiting health data', { exact: true })).toBeVisible()
  await expect(page.getByText('No service health reported.', { exact: true })).toBeVisible()
  await expect(page.getByText('No agent image readiness reported.', { exact: true })).toBeVisible()
  await expect(page.getByText('All services healthy', { exact: true })).toHaveCount(0)
})