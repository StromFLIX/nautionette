import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'

async function mockCopilot (context, configured = false) {
  await mockAgents(context)
  const integration = {
    instance: 'copilot', type: 'copilot', name: 'GitHub Copilot', configured,
    description: 'Sign in with GitHub to use models included in your Copilot subscription.',
    fields: [
      { key: 'integration_id', label: 'Copilot integration ID', default: 'copilot-developer-cli', pattern: '[a-z0-9][a-z0-9._-]{0,63}' },
      { key: 'api_key', label: 'GitHub token', kind: 'secret', optional: true, pattern: '\\S+' }
    ],
    credential: { mode: configured ? 'stored' : 'gateway', variable: 'GH_COPILOT_TOKEN' },
    config: configured ? { integration_id: 'my-cli' } : {},
    model_match: 'copilot/*', model_count: 0,
    discovery: { ok: false, message: 'GitHub Copilot needs authorization.' }
  }
  const state = {
    integration, starts: [], polls: 0, cancels: 0, saves: [],
    result: { status: 'pending', interval: 1 },
    device: { id: 'login-session', user_code: 'ABCD-1234', verification_uri: 'https://github.com/login/device', expires_in: 60, interval: 1 }
  }
  await context.route('**/api/model-integrations**', async route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path.endsWith('/copilot/login')) {
      state.starts.push(route.request().postDataJSON())
      if (state.startGate) await state.startGate
      return route.fulfill({ json: state.device })
    }
    if (path.endsWith('/copilot/login/login-session')) {
      if (method === 'DELETE') {
        state.cancels++
        return route.fulfill({ status: 204 })
      }
      state.polls++
      if (state.failure) return route.fulfill({ status: state.failure.status, json: { detail: state.failure.message } })
      if (state.result.status === 'complete') {
        integration.configured = true
        integration.discovery = { ok: true, message: 'Discovered 1 model.' }
        integration.model_count = 1
        integration.credential = { mode: 'stored', variable: '' }
      }
      return route.fulfill({ json: state.result })
    }
    if (method === 'PUT') state.saves.push(route.request().postDataJSON())
    return route.fulfill({ json: {
      integrations: integration.configured ? [integration] : [],
      available: integration.configured ? [] : [integration],
      writable: true, storage_mode: 'hybrid'
    } })
  })
  return state
}

async function add (page) {
  await page.goto('/settings/agents')
  await page.getByRole('button', { name: 'Add integration', exact: true }).click()
  await page.getByRole('button', { name: /GitHub Copilot.*Sign in with GitHub/ }).click()
  await page.getByRole('button', { name: 'Sign in with GitHub', exact: true }).click()
  await expect(page.getByText('ABCD-1234')).toBeVisible()
}

for (const width of [1440, 390]) {
  test(`add Copilot through device sign-in at ${width}px`, async ({ page, context }) => {
    const state = await mockCopilot(context)
    await page.setViewportSize({ width, height: 900 })
    await add(page)
    expect(state.starts).toEqual([{ integration_id: 'copilot-developer-cli' }])
    await expect(page.getByRole('link', { name: 'Open GitHub to authorize' }))
      .toHaveAttribute('href', 'https://github.com/login/device')
    await expect(page.getByLabel('GitHub token')).toBeDisabled()
    state.result = { status: 'complete' }
    await expect(page.getByText('GitHub Copilot signed in.', { exact: false })).toBeVisible()
    await expect(page.locator('.integration-card').getByText('1 models', { exact: true })).toBeVisible()
    await expect(page.getByText('ABCD-1234')).toHaveCount(0)
    expect(state.saves).toEqual([])
  })
}

test('reconnect an existing Copilot integration directly from its card', async ({ page, context }) => {
  const state = await mockCopilot(context, true)
  await page.goto('/settings/agents')
  await page.locator('.integration-card').getByRole('button', { name: 'Sign in with GitHub' }).click()
  await expect(page.getByText('ABCD-1234')).toBeVisible()
  expect(state.starts).toEqual([{ integration_id: 'my-cli' }])
  await expect(page.getByRole('button', { name: 'Save configuration' })).toBeDisabled()
  state.result = { status: 'complete' }
  await expect(page.getByText('GitHub Copilot signed in.', { exact: false })).toBeVisible()
})

test('denied authorization explains the failure and permits a fresh sign-in', async ({ page, context }) => {
  const state = await mockCopilot(context)
  state.failure = { status: 400, message: 'GitHub sign-in was denied. Sign in again when you are ready.' }
  await add(page)
  await expect(page.getByRole('alert')).toContainText('GitHub sign-in was denied.')
  await expect(page.getByText('ABCD-1234')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Sign in with GitHub', exact: true })).toBeEnabled()
  state.failure = null
  await page.getByRole('button', { name: 'Sign in with GitHub', exact: true }).click()
  await expect(page.getByText('ABCD-1234')).toBeVisible()
  expect(state.starts).toHaveLength(2)
})

test('temporary failure can be retried without starting authorization again', async ({ page, context }) => {
  const state = await mockCopilot(context)
  state.failure = { status: 502, message: 'agentgateway is unavailable' }
  await add(page)
  await expect(page.getByRole('alert')).toContainText('agentgateway is unavailable')
  state.failure = null
  state.result = { status: 'complete' }
  await page.getByRole('button', { name: 'Retry connection' }).click()
  await expect(page.getByText('GitHub Copilot signed in.', { exact: false })).toBeVisible()
  expect(state.starts).toHaveLength(1)
})

test('cancelling stops polling and re-enables manual configuration', async ({ page, context }) => {
  const state = await mockCopilot(context)
  await page.clock.install()
  await add(page)
  await page.getByRole('button', { name: 'Cancel sign-in', exact: true }).click()
  await expect(page.getByText('ABCD-1234')).toHaveCount(0)
  await page.clock.runFor(5000)
  expect(state.cancels).toBe(1)
  expect(state.polls).toBe(0)
  await expect(page.getByLabel('GitHub token')).toBeEnabled()
})

test('expired device codes show an actionable error', async ({ page, context }) => {
  const state = await mockCopilot(context)
  state.device.expires_in = 1
  await add(page)
  await expect(page.getByRole('alert')).toContainText('device code expired')
  await expect(page.getByText('ABCD-1234')).toHaveCount(0)
  expect(state.cancels).toBe(1)
})

test('leaving the form while starting cancels the late device session', async ({ page, context }) => {
  const state = await mockCopilot(context)
  let release
  state.startGate = new Promise(resolve => { release = resolve })
  await page.goto('/settings/agents')
  await page.getByRole('button', { name: 'Add integration', exact: true }).click()
  await page.getByRole('button', { name: /GitHub Copilot.*Sign in with GitHub/ }).click()
  await page.getByRole('button', { name: 'Sign in with GitHub', exact: true }).click()
  await expect.poll(() => state.starts.length).toBe(1)
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  release()
  await expect.poll(() => state.cancels).toBe(1)
  expect(state.polls).toBe(0)
})
