import { test, expect } from '@playwright/test'
const url = 'https://workiq.svc.cloud.microsoft/mcp'
const config = { transport: 'http', url, client_id: 'public-client', scope: 'read offline_access',
  issuer: 'https://login.example.test', redirect_uri: 'http://localhost:12798', response_mode: 'query',
  discovery_fingerprint: 'f'.repeat(64), share_account: true }
const fields = [{ key: 'name', label: 'Name', pattern: '[a-z0-9][a-z0-9-]{0,23}', placeholder: 'linear' },
  { key: 'url', label: 'Endpoint URL', pattern: 'https?://.+', placeholder: 'https://mcp.example.com/mcp' },
  { key: 'token', label: 'Access token', kind: 'secret', optional: true, pattern: '.+' }]
async function mock (context) {
  const state = { starts: [], prepares: [], connects: [], callbacks: [], servers: [], profiles: {}, failConnect: false, failLogin: false, clientRequired: false }
  await context.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname, method = route.request().method()
    if (path === '/api/mcp-oauth/status') return route.fulfill({ json: { connections: Object.entries(state.profiles).map(([name, item]) => ({ name, ...item })) } })
    if (path === '/api/mcp-oauth/capabilities') return route.fulfill({ json: { available: false, http: { available: true } } })
    if (path.startsWith('/api/mcp-oauth/connect/')) {
      const payload = route.request().postDataJSON()
      state.connects.push(payload)
      if (state.clientRequired && !payload.client_id) return route.fulfill({ status: 400, json: { detail: { code: 'oauth_client_required', message: 'Enter your approved public client ID once in Advanced settings.' } } })
      if (state.failConnect) return route.fulfill({ status: 502, json: { detail: 'Connection temporarily unavailable' } })
      return route.fulfill({ json: { status: 'oauth_required', config: { ...config, url: payload.url } } })
    }
    if (path.startsWith('/api/mcp-oauth/prepare/')) {
      state.prepares.push(route.request().postDataJSON())
      return route.fulfill({ json: config })
    }
    if (path.startsWith('/api/mcp-oauth/start/')) { state.starts.push(route.request().postDataJSON()); return route.fulfill({ json: { id: 'http-login' } }) }
    if (path === '/api/mcp-oauth/login/http-login') {
      if (method === 'DELETE') return route.fulfill({ status: 204 })
      if (state.failLogin) return route.fulfill({ json: { status: 'failed', message: 'Provider unavailable; try again' } })
      if (state.callbacks.length) {
        state.profiles.workiq = { ...config, mode: 'https', status: 'connected', revision: 'new', refreshable: true }
        state.servers = [{ name: 'workiq', url, transport: 'http', managed: true, tool_count: 1, oauth: state.profiles.workiq, credential: { mode: 'stored' } }]
        return route.fulfill({ json: { status: 'complete' } })
      }
      return route.fulfill({ json: { status: 'authorizing', authorization: { id: 'capture', port: 12798, redirect_uri: config.redirect_uri,
        authorization_url: 'https://login.example.test/authorize', state: 'state', response_mode: 'query' } } })
    }
    if (path === '/api/mcp-oauth/callback/http-login') { state.callbacks.push(route.request().postDataJSON()); return route.fulfill({ json: { status: 'forwarded' } }) }
    if (path.startsWith('/api/mcp-servers')) {
      if (method === 'DELETE') { state.servers = []; state.profiles = {} }
      return route.fulfill({ json: { servers: state.servers, oauth_connections: state.profiles, fields, stdio_fields: [], writable: true } })
    }
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/system') return route.fulfill({ json: { version: 'test', components: [] } })
    if (path === '/api/settings') return route.fulfill({ json: { defaults: {}, settings: {} } })
    const key = path.split('/').pop()
    return route.fulfill({ json: key === 'catalog' ? { models: [], tools: [], agent_sets: [{ name: 'default' }] } : { [key]: [] } })
  })
  return state
}
async function native (context) {
  await context.addInitScript(() => {
    localStorage.setItem('nautionette.server', location.origin)
    localStorage.setItem('nautionette.token', 'test-token')
    globalThis.preparedPorts = []
    globalThis.nautionetteDesktop = { oauth: { version: 1,
      prepare: async redirect => { globalThis.preparedPorts.push(redirect); return { id: 'receiver', port: 12798, redirect_uri: redirect } },
      authorize: () => new Promise(resolve => { globalThis.finishLogin = () => resolve({ method: 'GET', fields: { state: 'state', code: 'synthetic-code' } }) }),
      cancel: async () => {}
    } }
  })
}
async function add (page) {
  await page.goto('/settings/mcp')
  await page.getByRole('button', { name: 'Add server', exact: true }).click()
  await page.getByPlaceholder('linear').fill('workiq')
  await page.getByPlaceholder('https://mcp.example.com/mcp').fill(url)
}
for (const width of [1440, 390]) {
  test(`one Connect action discovers and signs in at ${width}px`, async ({ page, context }) => {
    const state = await mock(context); await native(context)
    await page.setViewportSize({ width, height: 1000 }); await add(page)
    await expect(page.getByRole('button', { name: 'Use WorkIQ HTTPS' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Discover OAuth' })).toHaveCount(0)
    await expect(page.getByRole('checkbox')).toHaveCount(0)
    await expect(page.getByLabel('Public OAuth client ID')).toBeHidden()
    if (width === 1440) await page.screenshot({ path: test.info().outputPath('mcp-simple-connect.png') })
    await page.locator('.integration-add').getByRole('button', { name: 'Connect', exact: true }).click()
    await expect(page.locator('.integration-add [role=status]')).toContainText('local port 12798')
    await page.evaluate(() => globalThis.finishLogin())
    await expect(page.getByText('workiq connected', { exact: true })).toBeVisible()
    expect(state.connects).toHaveLength(1); expect(state.starts).toHaveLength(1)
    expect(state.starts[0].client_id).toBe('public-client')
    expect(state.callbacks).toHaveLength(1)
  })
}

test('retry keeps the endpoint and last configuration without extra buttons', async ({ page, context }) => {
  const state = await mock(context); await native(context); await add(page)
  state.failConnect = true
  await page.locator('.integration-add').getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('temporarily unavailable')
  await expect(page.getByPlaceholder('https://mcp.example.com/mcp')).toHaveValue(url)
  state.failConnect = false
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await expect(page.locator('.integration-add [role=status]')).toContainText('local port')
  expect(state.connects[0]).toEqual(state.connects[1])
  await page.evaluate(() => globalThis.finishLogin())
  await expect(page.getByText('workiq connected', { exact: true })).toBeVisible()
})

test('expiration on a chat shows only a compact Login prompt and reuses saved settings', async ({ page, context }) => {
  const state = await mock(context); await native(context)
  state.profiles.workiq = { ...config, mode: 'https', status: 'reauth_required', revision: 'expired-1' }
  await page.goto('/chats')
  const dialog = page.locator('.mcp-login-dialog')
  await expect(dialog.getByRole('heading')).toHaveText('Sign in to workiq')
  await page.screenshot({ path: test.info().outputPath('mcp-relogin-dialog.png'), animations: 'disabled' })
  await expect(page.locator('.integration-add')).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Login', exact: true }).click()
  await expect(dialog.getByRole('status')).toContainText('local port')
  expect(state.prepares).toEqual([{}])
  await page.evaluate(() => globalThis.finishLogin())
  await expect(dialog).toHaveCount(0)
  await expect(page).toHaveURL('/chats')
})

test('manual sign-in stays in a popup; Snooze does not open a browser or setup form', async ({ page, context }) => {
  const state = await mock(context); await native(context)
  state.profiles.workiq = { ...config, mode: 'https', status: 'connected', revision: 'connected-1' }
  state.servers = [{ name: 'workiq', url, transport: 'http', managed: true, tool_count: 1, oauth: state.profiles.workiq }]
  await page.goto('/settings/mcp')
  await page.getByRole('button', { name: 'Sign in again' }).click()
  await expect(page.locator('.mcp-login-dialog')).toBeVisible()
  await page.locator('.mcp-login-dialog').getByRole('button', { name: 'Snooze', exact: true }).click()
  expect(state.starts).toEqual([])
  await expect(page.locator('.integration-add')).toHaveCount(0)
})

test('generic providers request a client ID only once and reveal the right advanced fields', async ({ page, context }) => {
  const state = await mock(context); await native(context); await add(page)
  state.clientRequired = true
  await page.getByPlaceholder('https://mcp.example.com/mcp').fill('https://mcp.example.test/mcp')
  await page.locator('.integration-add').getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('approved public client ID')
  await page.getByLabel('Public OAuth client ID').fill('approved-client')
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await expect(page.locator('.integration-add [role=status]')).toContainText('local port')
  expect(state.connects[1].client_id).toBe('approved-client')
  expect(state.connects[1].url).toBe('https://mcp.example.test/mcp')
  await page.evaluate(() => globalThis.finishLogin())
})

test('hosted OAuth in a web client explains desktop compatibility and never starts login', async ({ page, context }) => {
  const state = await mock(context); await add(page)
  await page.locator('.integration-add').getByRole('button', { name: 'Connect', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('desktop app')
  expect(state.starts).toEqual([])
})

test('unsupported devices persist a timed snooze and can resume it from settings', async ({ page, context }) => {
  const state = await mock(context)
  state.profiles.workiq = { ...config, mode: 'https', status: 'reauth_required', revision: 'expired' }
  await page.goto('/chats')
  const dialog = page.locator('.mcp-login-dialog')
  await expect(dialog.getByRole('button', { name: 'Login', exact: true })).toBeDisabled()
  await page.getByLabel('Snooze login reminders on this device').selectOption('604800000')
  await dialog.getByRole('button', { name: 'Snooze', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  const stored = await page.evaluate(() => localStorage.getItem('nautionette.mcp-login-snooze'))
  expect(stored).not.toContain('test-token')
  await page.reload()
  await expect(dialog).toHaveCount(0)
  await page.goto('/settings/mcp')
  await expect(page.locator('.integration-snooze')).toContainText('Other devices are unaffected')
  await page.getByRole('button', { name: 'Resume login reminders' }).click()
  await expect(dialog).toBeVisible()
  expect(state.starts).toEqual([])
})

test('snooze on one device does not suppress another device\'s reminders', async ({ page, context, browser }) => {
  const state = await mock(context); await native(context)
  state.profiles.workiq = { ...config, mode: 'https', status: 'reauth_required', revision: 'expired' }
  await page.goto('/chats')
  await page.locator('.mcp-login-dialog').getByRole('button', { name: 'Snooze', exact: true }).click()
  await expect(page.locator('.mcp-login-dialog')).toHaveCount(0)
  const second = await browser.newContext()
  try {
    const other = await mock(second)
    other.profiles.workiq = { ...state.profiles.workiq }
    const otherPage = await second.newPage()
    await otherPage.goto(new URL('/chats', page.url()).href)
    await expect(otherPage.locator('.mcp-login-dialog')).toBeVisible()
  } finally { await second.close() }
})

test('failed relogin retries saved setup in the same popup', async ({ page, context }) => {
  const state = await mock(context); await native(context)
  state.profiles.workiq = { ...config, mode: 'https', status: 'reauth_required', revision: 'expired' }
  state.failLogin = true
  await page.goto('/chats')
  const dialog = page.locator('.mcp-login-dialog')
  await dialog.getByRole('button', { name: 'Login', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('Provider unavailable')
  state.failLogin = false
  await dialog.getByRole('button', { name: 'Try again', exact: true }).click()
  await expect(dialog.getByRole('status')).toContainText('local port')
  await page.evaluate(() => globalThis.finishLogin())
  await expect(dialog).toHaveCount(0)
  expect(state.prepares).toEqual([{}, {}])
})
