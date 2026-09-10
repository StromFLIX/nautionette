import { test, expect } from '@playwright/test'

const name = { key: 'name', label: 'Name', pattern: '[a-z0-9][a-z0-9-]{0,23}', placeholder: 'linear' }
const fields = [name,
  { key: 'url', label: 'Endpoint URL', pattern: 'https?://.+', placeholder: 'https://mcp.example.com/mcp' },
  { key: 'token', label: 'Access token', kind: 'secret', pattern: '.+', optional: true }
]
const stdioFields = [name,
  { key: 'command', label: 'Command', pattern: '[A-Za-z0-9._/-]+', placeholder: 'npx' }
]

async function mockMcp (context) {
  const state = { servers: [], writes: [], fail: false, tests: [] }
  await context.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path.startsWith('/api/mcp-servers')) {
      const name = path.split('/')[3]
      if (path.endsWith('/test')) {
        state.tests.push(name)
        return route.fulfill({ json: { ok: true, status: null, message: 'Process answered with 1 tools.' } })
      }
      if (route.request().method() === 'PUT') {
        const payload = route.request().postDataJSON()
        state.writes.push(payload)
        if (state.fail) return route.fulfill({ status: 400, json: { detail: 'The stdio process did not answer MCP.' } })
        state.servers = [{ ...payload, name, managed: true, tool_count: 1,
          credential: { mode: 'none', variable: '' },
          env: Object.fromEntries(Object.keys(payload.env || {}).map(key => [key, null])) }]
      }
      if (route.request().method() === 'DELETE') state.servers = []
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

for (const width of [1440, 320]) {
  test(`stdio settings add, preserve secrets, test and remove at ${width}px`, async ({ page, context }) => {
    const state = await mockMcp(context)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/settings/mcp')
    await page.getByRole('button', { name: 'Add server', exact: true }).click()
    await page.getByLabel('Transport', { exact: true }).selectOption('stdio')
    await page.getByPlaceholder('linear').fill('brave')
    await page.getByPlaceholder('npx', { exact: true }).fill('npx')
    const args = ['-y', '@brave/brave-search-mcp-server', '--transport', 'stdio']
    await page.getByRole('textbox', { name: /Arguments/ }).fill(JSON.stringify(args))
    await page.getByRole('button', { name: 'Add variable', exact: true }).click()
    await page.getByLabel('Variable 1 name', { exact: true }).fill('BRAVE_API_KEY')
    await page.getByLabel('Variable 1 value', { exact: true }).fill('test-secret')
    await expect(page.getByLabel('Variable 1 value', { exact: true })).toHaveAttribute('type', 'password')
    await expect(page.getByText(/Only run trusted packages/)).toBeVisible()
    await page.locator('.integration-add').getByRole('button', { name: 'Add server', exact: true }).click()
    await expect.poll(() => state.writes.length).toBe(1)
    expect(state.writes[0]).toEqual({ transport: 'stdio', command: 'npx', args, env: { BRAVE_API_KEY: 'test-secret' } })
    await page.reload()
    await page.getByRole('button', { name: 'Configure', exact: true }).click()
    await expect(page.getByRole('textbox', { name: /Arguments/ })).toHaveValue(JSON.stringify(args, null, 2))
    await expect(page.getByLabel('Variable 1 value', { exact: true })).toHaveValue('')
    await page.getByRole('button', { name: 'Save server', exact: true }).click()
    await expect.poll(() => state.writes.length).toBe(2)
    expect(state.writes[1].env).toEqual({ BRAVE_API_KEY: null })
    await page.getByRole('button', { name: 'Test', exact: true }).click()
    await expect(page.getByText('Process answered with 1 tools.')).toBeVisible()
    await page.getByRole('button', { name: 'Configure', exact: true }).click()
    await page.getByRole('button', { name: 'Remove variable 1', exact: true }).click()
    await page.getByRole('button', { name: 'Save server', exact: true }).click()
    await expect.poll(() => state.writes.length).toBe(3)
    expect(state.writes[2].env).toEqual({})
    await page.getByRole('button', { name: 'Remove', exact: true }).click()
    await page.getByRole('button', { name: 'OK', exact: true }).click()
    await expect(page.getByText('No MCP server is configured.')).toBeVisible()
    expect(errors).toEqual([])
  })
}

test('invalid launch input cannot save; failed probes keep the draft; HTTP remains available', async ({ page, context }) => {
  const state = await mockMcp(context)
  await page.goto('/settings/mcp')
  await page.getByRole('button', { name: 'Add server', exact: true }).click()
  await page.getByLabel('Transport', { exact: true }).selectOption('stdio')
  await page.getByPlaceholder('linear').fill('brave')
  await page.getByPlaceholder('npx', { exact: true }).fill('npx')
  const args = page.getByRole('textbox', { name: /Arguments/ })
  const save = page.locator('.integration-add').getByRole('button', { name: 'Add server', exact: true })
  await args.fill('not-json')
  await expect(save).toBeDisabled()
  await args.fill('[123]')
  await expect(save).toBeDisabled()
  await args.fill('[]')
  await page.getByRole('button', { name: 'Add variable', exact: true }).click()
  await page.getByLabel('Variable 1 name', { exact: true }).fill('KEY')
  await page.getByRole('button', { name: 'Add variable', exact: true }).click()
  await page.getByLabel('Variable 2 name', { exact: true }).fill('KEY')
  await expect(save).toBeDisabled()
  await page.getByRole('button', { name: 'Remove variable 2', exact: true }).click()
  state.fail = true
  await save.click()
  await expect(page.getByText('The stdio process did not answer MCP.')).toBeVisible()
  await expect(page.getByPlaceholder('npx', { exact: true })).toHaveValue('npx')
  state.fail = false
  await page.getByLabel('Transport', { exact: true }).selectOption('http')
  await page.getByPlaceholder('https://mcp.example.com/mcp').fill('https://mcp.example.test/mcp')
  await save.click()
  await expect.poll(() => state.writes.length).toBe(2)
  expect(state.writes[1]).toEqual({ transport: 'http', url: 'https://mcp.example.test/mcp', token: '' })
})
