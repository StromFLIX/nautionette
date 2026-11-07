import { test, expect } from '@playwright/test'
const url = 'https://workiq.svc.cloud.microsoft/mcp'
const clientId = 'ba081686-5d24-4bc6-a0d6-d034ecffed87'
const fields = [
  { key: 'name', label: 'Name', pattern: '[a-z0-9][a-z0-9-]{0,23}', placeholder: 'linear' },
  { key: 'url', label: 'Endpoint URL', pattern: 'https?://.+', placeholder: 'https://mcp.example.com/mcp' },
  { key: 'token', label: 'Access token', kind: 'secret', optional: true, pattern: '.+' }
]
async function mock (context) {
  const state = { starts: [], callbacks: [], saves: [], discovers: 0, servers: [], profiles: {} }
  await context.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path === '/api/mcp-oauth/capabilities') return route.fulfill({ json: {
      available: false, message: 'CLI helper unavailable', desktop_bridge_version: 1,
      http: { available: true, workiq: { url, client_id: clientId, redirect_uri: 'http://localhost:12798' } }
    } })
    if (path === '/api/mcp-oauth/discover') {
      state.discovers++
      return route.fulfill({ json: { url, issuer: 'https://login.microsoftonline.com/organizations/v2.0',
        client_id: clientId, scope: 'ask offline_access openid', redirect_uri: 'http://localhost:12798', response_mode: 'query',
        discovery_fingerprint: 'f'.repeat(64), client_registration_verified: false } })
    }
    if (path.startsWith('/api/mcp-oauth/start/')) {
      state.starts.push(route.request().postDataJSON())
      return route.fulfill({ json: { id: 'http-login', status: 'authorizing' } })
    }
    if (path === '/api/mcp-oauth/login/http-login') {
      if (method === 'DELETE') return route.fulfill({ status: 204 })
      if (state.callbacks.length) {
        const profile = { mode: 'https', url, client_id: clientId, scope: 'ask offline_access openid',
          redirect_uri: 'http://localhost:12798', response_mode: 'query', status: 'connected', refreshable: true }
        state.profiles.workiq = profile
        state.servers = [{ name: 'workiq', url, transport: 'http', managed: true, tool_count: 2, credential: { mode: 'stored' }, oauth: profile }]
        return route.fulfill({ json: { status: 'complete', message: 'HTTPS OAuth connected and MCP handshake passed.' } })
      }
      return route.fulfill({ json: { status: 'authorizing', authorization: { id: 'http-capture', port: 12798,
        redirect_uri: 'http://localhost:12798', authorization_url: 'https://login.example.test/authorize',
        state: 'state-for-test', response_mode: 'query' } } })
    }
    if (path === '/api/mcp-oauth/callback/http-login') {
      expect(new URL(route.request().url()).search).toBe('')
      state.callbacks.push(route.request().postDataJSON())
      return route.fulfill({ json: { status: 'forwarded' } })
    }
    if (path.startsWith('/api/mcp-servers')) {
      if (method === 'PUT') state.saves.push(route.request().postDataJSON())
      if (method === 'DELETE') { state.servers = []; state.profiles = {} }
      return route.fulfill({ json: { servers: state.servers, oauth_connections: state.profiles, fields, stdio_fields: [], writable: true, storage_mode: 'hybrid' } })
    }
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/system') return route.fulfill({ json: { version: 'test', components: [] } })
    if (path === '/api/settings') return route.fulfill({ json: { defaults: {}, settings: {} } })
    const key = path.split('/').pop()
    return route.fulfill({ json: key === 'catalog' ? { models: [], tools: [], agent_sets: [{ name: 'default' }] } : { [key]: [] } })
  })
  return state
}

async function desktop (page, width) {
  await page.setViewportSize({ width, height: 1000 })
  await page.goto('/settings/general')
  await page.evaluate(() => {
    globalThis.prepared = []
    globalThis.nautionetteDesktop = { oauth: { version: 1,
      prepare: async redirect => { globalThis.prepared.push(redirect); return { id: 'receiver', redirect_uri: redirect, port: 12798 } },
      authorize: () => new Promise(resolve => { globalThis.finishHosted = () => resolve({ method: 'GET', fields: { state: 'state-for-test', code: 'private-test-code' } }) }),
      cancel: async () => {}
    } }
  })
  if (width > 900) await page.locator('.settings__tree').getByRole('link', { name: 'MCP servers', exact: true }).click()
  else {
    await page.getByRole('button', { name: 'Settings category', exact: true }).click()
    await page.getByRole('button', { name: 'MCP servers', exact: true }).click()
  }
}
async function form (page) {
  await page.getByRole('button', { name: 'Add server', exact: true }).click()
  await page.getByLabel('Authentication', { exact: true }).selectOption('oauth')
  await page.getByRole('button', { name: 'Use WorkIQ HTTPS', exact: true }).click()
}

for (const width of [1440, 390]) {
  test(`hosted WorkIQ OAuth connects without CLI at ${width}px`, async ({ page, context }) => {
    const state = await mock(context)
    await desktop(page, width)
    await form(page)
    await expect(page.getByPlaceholder('https://mcp.example.com/mcp')).toHaveValue(url)
    await expect(page.getByLabel('Transport', { exact: true })).toHaveValue('http')
    await expect(page.getByRole('button', { name: 'Connect with HTTPS OAuth' })).toBeDisabled()
    await expect(page.getByText(/No WorkIQ CLI, npx process or container browser/)).toBeVisible()
    await page.getByLabel('Share OAuth account with this instance').check()
    await page.getByRole('button', { name: 'Connect with HTTPS OAuth' }).click()
    await expect(page.locator('.integration-add [role="status"]')).toContainText('local port 12798')
    expect(await page.evaluate(() => globalThis.prepared)).toEqual(['http://localhost:12798'])
    expect(state.starts[0].client_id).toBe(clientId)
    expect(state.starts[0].command).toBeUndefined()
    expect(state.starts[0].args).toBeUndefined()
    await page.evaluate(() => globalThis.finishHosted())
    await expect(page.getByText('workiq: HTTPS OAuth connected and MCP checked')).toBeVisible()
    expect(state.callbacks).toEqual([{ capture_id: 'http-capture', method: 'GET', fields: { state: 'state-for-test', code: 'private-test-code' } }])
    expect(state.saves).toEqual([])
    expect(await page.evaluate(() => JSON.stringify({ ...localStorage }))).not.toContain('private-test-code')
  })
}

test('web cannot start hosted OAuth even when the backend supports it', async ({ page, context }) => {
  const state = await mock(context)
  await page.goto('/settings/mcp')
  await form(page)
  await page.getByLabel('Share OAuth account with this instance').check()
  await expect(page.getByRole('alert')).toContainText('Switch from the web/mobile client')
  await expect(page.getByRole('button', { name: 'Connect with HTTPS OAuth' })).toBeDisabled()
  expect(state.starts).toEqual([])
})

test('expired OAuth connections retain a removable reconnect profile', async ({ page, context }) => {
  const state = await mock(context)
  state.profiles.workiq = { mode: 'https', url, client_id: clientId, redirect_uri: 'http://localhost:12798', scope: 'ask', response_mode: 'query', status: 'reauth_required' }
  await page.goto('/settings/mcp')
  await expect(page.getByText(/HTTPS OAuth · reauth_required/)).toBeVisible()
  await page.getByRole('button', { name: 'Remove saved connection' }).click()
  await page.getByRole('button', { name: 'OK', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Remove saved connection' })).toHaveCount(0)
})
