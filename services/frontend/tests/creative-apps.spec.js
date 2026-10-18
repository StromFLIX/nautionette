import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'

const names = ['PhotoCraft', 'VectorCraft', 'FilmCraft', 'LightCraft', 'PdfCraft', 'EffectCraft', 'DesignCraft', 'FigCraft', 'WordCraft', 'SoundCraft', 'DeckCraft', 'CADCraft', 'GridCraft']
async function mockApps (context) {
  await mockAgents(context)
  const state = { writes: [], fail: false, outage: false, releaseOutage: false, apps: names.map(name => ({
    id: name.toLowerCase(), name, description: `Native ${name} headless tools`, repository: `https://github.com/storytold/${name.toLowerCase()}`,
    version: '0.8.0', bundled_version: '0.8.0', bundled: true, enabled: true, revision: 0, status: 'ready', pending_version: null, error: ''
  })) }
  state.finish = (id, error = '') => {
    const app = state.apps.find(app => app.id === id)
    if (!error) { app.version = app.pending_version; app.bundled = false }
    Object.assign(app, { pending_version: null, status: 'ready', error, revision: app.revision + 1 })
  }
  await context.route('**/api/artcraft/apps**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    if (path === '/api/artcraft/apps') return state.outage ? route.fulfill({ status: 503, json: { detail: 'Apps unavailable' } }) : route.fulfill({ json: { apps: state.apps, architecture: 'x86_64' } })
    const id = path.split('/')[4], app = state.apps.find(item => item.id === id)
    if (path.endsWith('/releases')) return state.releaseOutage ? route.fulfill({ status: 502, json: { detail: 'Release lookup rate limited; retry later' } }) : route.fulfill({ json: { versions: ['1.0.0', '0.8.0'] } })
    const body = request.postDataJSON(); state.writes.push({ id, ...body })
    if (state.fail) return route.fulfill({ status: 422, json: { detail: 'Release failed validation' } })
    if (body.expected_revision !== app.revision) return route.fulfill({ status: 409, json: { detail: 'App settings changed; reload before saving' } })
    if ('enabled' in body) app.enabled = body.enabled
    if (body.version === 'bundled') Object.assign(app, { version: app.bundled_version, bundled: true, error: '' })
    else if (body.version) Object.assign(app, { pending_version: body.version, status: 'installing', error: '' })
    app.revision += 1
    return route.fulfill({ json: app })
  })
  return state
}

for (const width of [1440, 320]) {
  test(`all 13 native apps, enable controls and version update/rollback work at ${width}px`, async ({ page, context }) => {
    const state = await mockApps(context)
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/settings/creative-apps')
    await expect(page.getByRole('heading', { name: 'Creative apps', exact: true })).toBeVisible()
    await expect(page.getByRole('article')).toHaveCount(13)
    const app = page.getByRole('article', { name: 'CADCraft', exact: true })
    await app.getByRole('checkbox', { name: 'Enable CADCraft', exact: true }).uncheck()
    await expect(app.getByRole('checkbox')).not.toBeChecked()
    expect(state.writes[0]).toEqual({ id: 'cadcraft', enabled: false, expected_revision: 0 })
    await app.getByRole('button', { name: 'Check releases', exact: true }).click()
    await expect(app).toContainText('Available: 1.0.0, 0.8.0')
    await app.getByLabel('Pinned version', { exact: true }).fill('1.0.0')
    await app.getByRole('button', { name: 'Apply version', exact: true }).click()
    await expect(app.getByRole('status')).toContainText('Installing and verifying 1.0.0')
    await expect(app).toContainText('Active: 0.8.0')
    await expect(app.getByLabel('Pinned version', { exact: true })).toBeDisabled()
    state.finish('cadcraft')
    await expect(app).toContainText('Active: 1.0.0')
    await expect(app).toContainText('Verified update')
    await app.getByRole('button', { name: 'Use bundled 0.8.0', exact: true }).click()
    await expect(app).toContainText('Active: 0.8.0')
    await expect(app.getByRole('button', { name: 'Use bundled 0.8.0', exact: true })).toHaveCount(0)
    expect(state.writes.at(-1)).toEqual({ id: 'cadcraft', version: 'bundled', expected_revision: 3 })
    expect(await page.locator('.settings__body').evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
  })
}

test('failed verification preserves active version, shows actionable error and allows retry', async ({ page, context }) => {
  const state = await mockApps(context)
  await page.goto('/settings/creative-apps')
  const app = page.getByRole('article', { name: 'CADCraft', exact: true })
  await app.getByLabel('Pinned version', { exact: true }).fill('1.0.0')
  await app.getByRole('button', { name: 'Apply version', exact: true }).click()
  await expect(app.getByRole('status')).toBeVisible()
  state.finish('cadcraft', 'Update failed verification. The previous version is unchanged.')
  await expect(app.getByRole('alert')).toContainText('previous version is unchanged')
  await expect(app).toContainText('Active: 0.8.0')
  await expect(app.getByLabel('Pinned version', { exact: true })).toHaveValue('1.0.0')
  await app.getByRole('button', { name: 'Apply version', exact: true }).click()
  await expect(app.getByRole('status')).toBeVisible()
  state.finish('cadcraft')
  await expect(app).toContainText('Active: 1.0.0')
  await expect(app.getByRole('alert')).toHaveCount(0)
})

test('load/release outages are retryable and rejected saves retain version drafts', async ({ page, context }) => {
  const state = await mockApps(context)
  state.outage = true
  await page.goto('/settings/creative-apps')
  await expect(page.getByRole('alert')).toContainText('Apps unavailable')
  state.outage = false
  await page.getByRole('button', { name: 'Retry loading', exact: true }).click()
  const app = page.getByRole('article', { name: 'CADCraft', exact: true })
  state.releaseOutage = true
  await app.getByRole('button', { name: 'Check releases', exact: true }).click()
  await expect(app.getByRole('alert')).toContainText('rate limited')
  state.releaseOutage = false
  await app.getByRole('button', { name: 'Check releases', exact: true }).click()
  await expect(app).toContainText('Available: 1.0.0')
  state.fail = true
  await app.getByLabel('Pinned version', { exact: true }).fill('1.0.0')
  await app.getByRole('button', { name: 'Apply version', exact: true }).click()
  await expect(app.getByRole('alert')).toContainText('Release failed validation')
  await expect(app.getByLabel('Pinned version', { exact: true })).toHaveValue('1.0.0')
  await expect(app).toContainText('Active: 0.8.0')
})

test('settings search discovers native apps without visiting the pane first', async ({ page, context }) => {
  await mockApps(context)
  await page.goto('/settings/general')
  await page.getByRole('searchbox', { name: 'Search settings', exact: true }).fill('CADCraft')
  await page.getByRole('link').filter({ hasText: 'ArtCraft applications' }).click()
  await expect(page).toHaveURL(/\/settings\/creative-apps#creative-apps/)
  await expect(page.getByRole('article', { name: 'CADCraft', exact: true })).toBeVisible()
})
