import { test, expect } from '@playwright/test'
async function mock (context) {
  const state = { starts: [], callbacks: [], available: true }
  const name = { key: 'name', label: 'Name', pattern: '[a-z0-9][a-z0-9-]{0,23}', placeholder: 'linear' }
  await context.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/mcp-oauth/status') return route.fulfill({ json: { connections: [] } })
    if (path === '/api/mcp-oauth/capabilities') return route.fulfill({ json: { available: state.available, workiq: { args: ['-y', '@microsoft/workiq@1.0.1', 'mcp'], eula_url: 'https://example.test/license' } } })
    if (path.startsWith('/api/mcp-oauth/start/')) { state.starts.push(route.request().postDataJSON()); return route.fulfill({ json: { id: 'login' } }) }
    if (path === '/api/mcp-oauth/login/login') {
      if (route.request().method() === 'DELETE') return route.fulfill({ status: 204 })
      return route.fulfill({ json: state.callbacks.length ? { status: 'complete' } : { status: 'authorizing', authorization: { id: 'capture', port: 43123, redirect_uri: 'http://localhost:43123', authorization_url: 'https://login.example.test/authorize', state: 'state', response_mode: 'form_post' } } })
    }
    if (path === '/api/mcp-oauth/callback/login') { state.callbacks.push(route.request().postDataJSON()); return route.fulfill({ json: { status: 'forwarded' } }) }
    if (path.startsWith('/api/mcp-servers')) return route.fulfill({ json: { servers: [], fields: [name, { key: 'url', label: 'Endpoint URL', pattern: 'https?://.+', placeholder: 'https://mcp.example.com/mcp' }], stdio_fields: [name, { key: 'command', label: 'Command', pattern: '[A-Za-z0-9._/-]+', placeholder: 'npx' }], writable: true } })
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/settings') return route.fulfill({ json: { defaults: {}, settings: {} } })
    const key = path.split('/').pop()
    return route.fulfill({ json: key === 'catalog' ? { models: [], tools: [], agent_sets: [] } : { [key]: [] } })
  })
  return state
}
async function form (page) {
  await page.goto('/settings/mcp')
  await page.getByRole('button', { name: 'Add server', exact: true }).click()
  await page.getByText('Advanced settings', { exact: true }).click()
  await page.getByLabel('Transport', { exact: true }).selectOption('stdio')
  await page.getByLabel('Authentication', { exact: true }).selectOption('oauth')
  await page.getByLabel('CLI adapter', { exact: true }).selectOption('workiq')
  await page.getByPlaceholder('linear').fill('workiq')
}

test('CLI compatibility remains in Advanced settings with one-time license acceptance', async ({ page, context }) => {
  const state = await mock(context)
  await context.addInitScript(() => {
    localStorage.setItem('nautionette.server', location.origin)
    localStorage.setItem('nautionette.token', 'test')
    globalThis.nautionetteDesktop = { oauth: { version: 1, prepare: async redirect => ({ id: 'receiver', port: 43123, redirect_uri: redirect }), authorize: async () => ({ method: 'POST', fields: { state: 'state', code: 'synthetic-code' } }), cancel: async () => {} } }
  })
  await form(page)
  const connect = page.locator('.integration-add').getByRole('button', { name: 'Connect', exact: true })
  await expect(connect).toBeDisabled()
  await page.getByLabel('Accept WorkIQ EULA').check()
  await connect.click()
  await expect(page.getByText('workiq connected', { exact: true })).toBeVisible()
  expect(state.starts[0].accept_eula).toBe(true)
  expect(state.starts[0].args).toEqual(['-y', '@microsoft/workiq@1.0.1', 'mcp'])
  expect(state.callbacks).toEqual([{ capture_id: 'capture', method: 'POST', fields: { state: 'state', code: 'synthetic-code' } }])
})

test('web client cannot start CLI OAuth', async ({ page, context }) => {
  const state = await mock(context)
  await form(page)
  await page.getByLabel('Accept WorkIQ EULA').check()
  await page.locator('.integration-add').getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('desktop app')
  expect(state.starts).toEqual([])
})
