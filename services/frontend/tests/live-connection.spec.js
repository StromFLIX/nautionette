import { createServer } from 'node:http'
import { test, expect } from '@playwright/test'
import { mockDesign } from './design-fixture.js'

// Real, persistent HTTP/SSE responses: route.fulfill closes the response and
// would test repeated fixture EOFs instead of a healthy connection and an outage.
async function liveBackend (context) {
  const state = await mockDesign(context)
  const clients = new Set(), requests = []
  let status = 200
  const frame = data => `data: ${JSON.stringify(data)}\n\n`
  const server = createServer((request, response) => {
    const path = new URL(request.url, 'http://localhost').pathname
    response.setHeader('Access-Control-Allow-Origin', '*')
    response.setHeader('Access-Control-Allow-Headers', 'Authorization')
    response.setHeader('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
    if (request.method === 'OPTIONS') { response.writeHead(204); response.end(); return }
    if (path === '/healthz') { response.setHeader('Content-Type', 'application/json'); response.end('{"status":"ok","environment":"production"}'); return }
    requests.push({ path, method: request.method })
    if (status !== 200) { response.writeHead(status); response.end(); return }
    if (request.method === 'HEAD') { response.writeHead(204); response.end(); return }
    response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
    response.write(frame(path === '/api/events' ? { kind: 'connected' } : { type: 'snapshot', ...state.data }))
    const client = { path, response }
    clients.add(client)
    response.on('close', () => clients.delete(client))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const url = `http://127.0.0.1:${server.address().port}`
  await context.addInitScript(url => localStorage.setItem('nautionette.server', url), url)
  await context.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname
    return path === '/api/events' || path.endsWith('/stream') ? route.continue() : route.fallback()
  })
  return {
    state, requests, clients,
    set status (value) { status = value },
    disconnect () { for (const { response } of clients) response.destroy() },
    emit (path, data) { for (const client of clients) if (client.path === path) client.response.write(frame(data)) },
    close () { server.closeAllConnections(); return new Promise(resolve => server.close(resolve)) }
  }
}

const notice = page => page.getByRole('status', { name: 'Live connection', exact: true })
const streams = backend => backend.requests.filter(request => request.method === 'GET')

// These also run in WebKit in CI: recovery must work on Safari/iOS, not just Chromium.
test('temporary HTTP 503 at connection time recovers chat and global updates without reload', async ({ context, page }) => {
  const backend = await liveBackend(context)
  try {
    backend.status = 503
    await page.goto('/chats/alpha')
    await expect(notice(page)).toContainText('Reconnecting automatically')
    await expect(page.locator('.pane-head')).toContainText('Reconnecting')
    await expect.poll(() => backend.requests.filter(request => request.method === 'HEAD').length).toBeGreaterThanOrEqual(2)
    backend.status = 200
    await expect.poll(() => backend.clients.size).toBe(2)
    await expect(notice(page)).toHaveCount(0)
    await expect(page.locator('.pane-head')).not.toContainText('Reconnecting')
    backend.state.data.chat.title = 'Recovered global updates'
    backend.emit('/api/events', { kind: 'chat.updated' })
    await expect(page.locator('.row-item__title').filter({ hasText: 'Recovered global updates' })).toBeVisible()
    backend.state.data.messages.push({ id: 'recovered', role: 'assistant', content: 'Recovered chat stream', meta: {} })
    backend.emit('/api/chats/alpha/stream', { type: 'snapshot', ...backend.state.data })
    await expect(page.locator('.thread__body')).toContainText('Recovered chat stream')
  } finally { await context.close(); await backend.close() }
})

test('a disconnect after connection is recovered without replaying a message or replacing the editor', async ({ context, page }) => {
  const backend = await liveBackend(context)
  try {
    await page.goto('/chats/alpha')
    await expect.poll(() => backend.clients.size).toBe(2)
    const editor = page.getByRole('textbox', { name: 'Message', exact: true })
    await editor.fill('Keep this unsent draft')
    const element = await editor.elementHandle()
    backend.status = 503
    backend.disconnect()
    await expect(notice(page)).toBeVisible()
    backend.status = 200
    await expect.poll(() => streams(backend).length).toBeGreaterThanOrEqual(4)
    await expect(notice(page)).toHaveCount(0)
    await expect(page.locator('.pane-head')).not.toContainText('Reconnecting')
    await expect(editor).toHaveValue('Keep this unsent draft')
    expect(await editor.evaluate((current, original) => current === original, element)).toBe(true)
  } finally { await context.close(); await backend.close() }
})

test('stalled OPEN streams recover when the watchdog expires', async ({ context, page }) => {
  const backend = await liveBackend(context)
  try {
    await page.clock.install()
    await page.goto('/chats/alpha')
    await expect.poll(() => backend.clients.size).toBe(2)
    await page.clock.fastForward(60_001)
    await expect(notice(page)).toBeVisible()
    await page.clock.fastForward(1001)
    await expect.poll(() => streams(backend).length).toBeGreaterThanOrEqual(4)
    await expect(notice(page)).toHaveCount(0)
    await expect(page.locator('.pane-head')).not.toContainText('Reconnecting')
  } finally { await context.close(); await backend.close() }
})

test('HTTP 401 stops both loops and asks for credentials; saving a new token reconnects both', async ({ context, page }) => {
  const backend = await liveBackend(context)
  try {
    await page.clock.install()
    await page.goto('/chats/alpha')
    await expect.poll(() => backend.clients.size).toBe(2)
    backend.status = 401
    backend.disconnect()
    await expect(notice(page)).toBeVisible()
    await page.clock.fastForward(1100)
    const token = page.getByPlaceholder('access token', { exact: true })
    await expect(token).toBeVisible()
    await expect.poll(() => backend.requests.filter(request => request.method === 'HEAD').length).toBe(2)
    const count = backend.requests.length
    await page.clock.fastForward(300_000)
    await page.evaluate(() => { window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('online')) })
    expect(backend.requests.length).toBe(count)
    backend.status = 200
    await token.fill('replacement-test-token')
    await page.getByRole('button', { name: 'Connect', exact: true }).click()
    await expect(token).toHaveCount(0)
    await expect.poll(() => backend.clients.size).toBe(2)
    await expect(notice(page)).toHaveCount(0)
    await expect(page.locator('.pane-head')).not.toContainText('Reconnecting')
  } finally { await context.close(); await backend.close() }
})
