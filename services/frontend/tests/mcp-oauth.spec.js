import { test, expect } from '@playwright/test'
const fields = [
  { key: 'name', label: 'Name', pattern: '[a-z0-9][a-z0-9-]{0,23}', placeholder: 'linear' },
  { key: 'url', label: 'Endpoint URL', pattern: 'https?://.+', placeholder: 'https://mcp.example.com/mcp' },
  { key: 'token', label: 'Access token', kind: 'secret', optional: true, pattern: '.+' }
]
const stdioFields = [fields[0], { key: 'command', label: 'Command', pattern: '[A-Za-z0-9._/-]+', placeholder: 'npx' }]
async function mock (context) {
  const state = { starts: [], callbacks: [], cancels: 0, saves: [], servers: [], available: true, uncertain: false }
  await context.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path === '/api/mcp-oauth/capabilities') return route.fulfill({ json: { available: state.available,
      message: state.available ? 'CLI OAuth available' : 'Deploy the matching gateway image.',
      workiq: { args: ['-y', '@microsoft/workiq@1.0.1', 'mcp'], eula_url: 'https://example.test/workiq-eula.docx' } } })
    if (path.startsWith('/api/mcp-oauth/start/')) {
      state.starts.push(route.request().postDataJSON())
      return route.fulfill({ json: { id: 'login', status: 'starting' } })
    }
    if (path === '/api/mcp-oauth/login/login') {
      if (method === 'DELETE') { state.cancels++; return route.fulfill({ status: 204 }) }
      if (state.callbacks.length && !state.uncertain) {
        const payload = state.starts[0]
        state.servers = [{ name: 'workiq', ...payload, env: { HOME: null }, managed: true, tool_count: 1,
          oauth: { mode: 'cli', provider: 'workiq', accept_eula: true }, credential: { mode: 'none' } }]
        return route.fulfill({ json: { status: 'complete', message: 'CLI login finished and MCP checked' } })
      }
      return route.fulfill({ json: { status: 'authorizing', authorization: { id: 'capture', port: 43123,
        redirect_uri: 'http://localhost:43123', authorization_url: 'https://login.example.test/authorize',
        state: 'private-state', response_mode: 'form_post' } } })
    }
    if (path === '/api/mcp-oauth/callback/login') {
      state.callbacks.push(route.request().postDataJSON())
      expect(new URL(route.request().url()).search).toBe('')
      return route.fulfill({ json: { status: state.uncertain ? 'uncertain' : 'forwarded' } })
    }
    if (path.startsWith('/api/mcp-servers')) {
      if (method === 'PUT') state.saves.push(route.request().postDataJSON())
      return route.fulfill({ json: { servers: state.servers, fields, stdio_fields: stdioFields, writable: true, storage_mode: 'hybrid' } })
    }
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/system') return route.fulfill({ json: { version: 'test', components: [] } })
    if (path === '/api/settings') return route.fulfill({ json: { defaults: {}, settings: {} } })
    const key = path.split('/').pop()
    return route.fulfill({ json: key === 'catalog' ? { models: [], tools: [], agent_sets: [{ name: 'default' }] } : { [key]: [] } })
  })
  return state
}

async function desktop (page, width = 1440) {
  // Keep browser test setup separate from the native instance-setup screen.
  // The packaged-app smoke test exercises the real preload/IPC independently.
  await page.setViewportSize({ width, height: 1000 })
  await page.goto('/settings/general')
  await page.evaluate(() => {
    globalThis.preparedPorts = []
    globalThis.nautionetteDesktop = { oauth: { version: 1,
      prepare: async redirect => { globalThis.preparedPorts.push(redirect); return { id: 'receiver', port: 43123 } },
      authorize: () => new Promise(resolve => { globalThis.finishOAuth = () => resolve({ method: 'POST', fields: { state: 'private-state', code: 'private-code', session_state: 'session' } }) }),
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
  await page.getByLabel('Transport', { exact: true }).selectOption('stdio')
  await page.getByLabel('Authentication', { exact: true }).selectOption('oauth')
  await page.getByRole('button', { name: 'Use WorkIQ preset', exact: true }).click()
}

for (const width of [1440, 390]) {
  test(`desktop WorkIQ OAuth detects port, requires EULA and forwards POST at ${width}px`, async ({ page, context }) => {
    const state = await mock(context)
    await desktop(page, width)
    await form(page)
    const connect = page.getByRole('button', { name: 'Connect with CLI OAuth', exact: true })
    await expect(connect).toBeDisabled()
    await expect(page.getByText(/shared across all chats and users/)).toBeVisible()
    await page.getByLabel('Accept WorkIQ EULA').check()
    await connect.click()
    await expect(page.locator('.integration-add [role="status"]')).toContainText('detected local port 43123')
    expect(await page.evaluate(() => globalThis.preparedPorts)).toEqual(['http://localhost:43123'])
    await page.evaluate(() => globalThis.finishOAuth())
    await expect(page.getByText('workiq: CLI login finished and MCP connection checked')).toBeVisible()
    expect(state.starts[0].accept_eula).toBe(true)
    expect(state.starts[0].args).toEqual(['-y', '@microsoft/workiq@1.0.1', 'mcp'])
    expect(state.callbacks).toEqual([{ capture_id: 'capture', method: 'POST', fields: { state: 'private-state', code: 'private-code', session_state: 'session' } }])
    expect(state.saves).toEqual([])
    expect(await page.evaluate(() => JSON.stringify({ ...localStorage }))).not.toContain('private-code')
  })
}

test('web client is told to switch and cannot start CLI OAuth', async ({ page, context }) => {
  const state = await mock(context)
  await page.goto('/settings/mcp')
  await form(page)
  await page.getByLabel('Accept WorkIQ EULA').check()
  await expect(page.getByRole('alert')).toContainText('Switch from the web/mobile client')
  await expect(page.getByRole('button', { name: 'Connect with CLI OAuth' })).toBeDisabled()
  expect(state.starts).toEqual([])
})

test('unavailable gateway blocks login and uncertain delivery is not replayed', async ({ page, context }) => {
  const state = await mock(context)
  state.available = false
  await desktop(page)
  await form(page)
  await page.getByLabel('Accept WorkIQ EULA').check()
  await expect(page.getByRole('alert')).toContainText('Deploy the matching gateway image')
  await expect(page.getByRole('button', { name: 'Connect with CLI OAuth' })).toBeDisabled()
  state.available = true
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  state.uncertain = true
  await page.getByRole('button', { name: 'Connect with CLI OAuth' }).click()
  await expect(page.locator('.integration-add [role="status"]')).toContainText('detected local port')
  await page.evaluate(() => globalThis.finishOAuth())
  await expect(page.getByText('Callback delivery is uncertain; start a fresh login. It will not be replayed.')).toBeVisible()
  await expect.poll(() => state.cancels).toBe(1)
  expect(state.callbacks).toHaveLength(1)
  expect(state.saves).toEqual([])
})
