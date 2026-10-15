import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==', 'base64')
const pdf = Buffer.from('%PDF-generated-output')
function output (revision = 1, overrides = {}) {
  return {
    id: 'a'.repeat(32), revision, title: 'Mounting bracket', summary: 'Drawing and editable source.', source_app: 'CADCraft', turn_id: 'turn', created_at: Date.now() / 1000,
    files: [
      { id: `pdf-${revision}`, name: 'bracket.pdf', size: pdf.length, mime_type: 'application/pdf', role: 'file', previewable: false },
      { id: `preview-${revision}`, name: 'preview.png', size: png.length, mime_type: 'image/png', role: 'preview', previewable: true },
      { id: `source-${revision}`, name: 'bracket.dxf', size: 100, mime_type: 'image/vnd.dxf', role: 'source', previewable: false }
    ], ...overrides
  }
}
async function fixture (context, { empty = false, inline = false } = {}) {
  const first = output()
  const chat = id => ({ id, title: id, agent_set: 'default', model: 'test/model', read_revision: 0, created_at: Date.now() / 1000, updated_at: Date.now() / 1000 })
  const state = {
    chats: {
      alpha: { chat: chat('alpha'), messages: [{ id: 'reply', role: 'assistant', content: 'Here is your result.', meta: inline ? { outputs: [first] } : {} }], outputs: empty ? [] : [first], active_turn: null },
      beta: { chat: chat('beta'), messages: [], outputs: [], active_turn: null }
    },
    history: [first], reads: [], files: [], completedFiles: 0, failPreview: false, failDownload: false, failHistory: false
  }
  await context.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/catalog') return route.fulfill({ json: { models: [], tools: [], agent_sets: [], default_model: 'test/model' } })
    if (path === '/api/system') return route.fulfill({ json: { components: [], agent_sets: [] } })
    if (path === '/api/chats') return route.fulfill({ json: { chats: Object.values(state.chats).map(data => data.chat) } })
    if (path === '/api/workflows') return route.fulfill({ json: { workflows: [] } })
    if (path === '/api/drafts') return route.fulfill({ json: { drafts: [] } })
    if (path === '/api/runs') return route.fulfill({ json: { runs: [] } })
    if (path.includes('/outputs/')) {
      if (path.endsWith('/revisions')) return state.failHistory ? route.fulfill({ status: 503, json: { detail: 'History unavailable' } }) : route.fulfill({ json: { revisions: state.history } })
      const preview = new URL(route.request().url()).searchParams.get('preview') === 'true'
      state.files.push({ path, preview, authorization: route.request().headers().authorization })
      if (preview ? state.failPreview : state.failDownload) return route.fulfill({ status: 503, json: { detail: 'File unavailable' } })
      if (state.waitFile) await state.waitFile
      await route.fulfill({ contentType: preview ? 'image/png' : 'application/octet-stream', body: preview ? png : pdf })
      state.completedFiles++
      return
    }
    const match = path.match(/^\/api\/chats\/([^/]+)(?:\/(stream|read-state))?$/)
    if (match) {
      const data = state.chats[match[1]]
      if (match[2] === 'stream') return route.fulfill({ contentType: 'text/event-stream', body: `retry: 100\ndata: ${JSON.stringify({ type: 'snapshot', ...data })}\n\n` })
      if (match[2] === 'read-state') {
        state.reads.push(route.request().postDataJSON())
        return route.fulfill({ json: data.chat })
      }
      return route.fulfill({ json: data })
    }
    return route.fulfill({ json: {} })
  })
  await context.addInitScript(() => localStorage.setItem('nautionette.token', 'output-test-token'))
  return state
}

const outputsTab = page => page.getByRole('tab', { name: /Outputs/ })
const outputPanel = page => page.getByRole('tabpanel', { name: /Outputs/ })

for (const width of [1440, 390]) {
  test(`outputs preview, download and composer state work at ${width}px without a browser app`, async ({ page, context }) => {
    const state = await fixture(context)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats/alpha')
    const composer = page.getByRole('textbox', { name: 'Message', exact: true })
    await composer.fill('Keep this draft')
    expect(state.files).toHaveLength(0)
    await outputsTab(page).click()
    const panel = outputPanel(page)
    await expect(panel.getByRole('heading', { name: 'Mounting bracket' })).toBeVisible()
    await expect(panel.getByText('Ready · Revision 1 · CADCraft')).toBeVisible()
    await expect(panel.locator('img')).toBeVisible()
    await expect.poll(() => panel.locator('img').evaluate(image => image.naturalWidth)).toBe(1)
    await panel.getByRole('button', { name: 'View preview.png', exact: true }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await page.getByRole('button', { name: 'Close preview', exact: true }).click()
    const downloaded = page.waitForEvent('download')
    await panel.getByRole('button', { name: 'Download bracket.pdf', exact: true }).click()
    const download = await downloaded
    expect(download.suggestedFilename()).toBe('bracket.pdf')
    expect(await readFile(await download.path())).toEqual(pdf)
    await expect(panel.getByText('Editable source ·', { exact: false })).toBeVisible()
    expect(state.files.every(file => file.authorization === 'Bearer output-test-token')).toBe(true)
    expect(state.files.filter(file => !file.preview)).toHaveLength(1)
    await expect(page.locator('iframe, canvas')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: `test-results/chat-outputs-${width}.png` })
    await page.getByRole('tab', { name: 'Chat', exact: true }).click()
    await expect(composer).toHaveValue('Keep this draft')
    expect(errors).toEqual([])
  })
}

test('empty state and new publications do not steal chat focus', async ({ page, context }) => {
  const state = await fixture(context, { empty: true })
  await page.goto('/chats/alpha')
  await outputsTab(page).click()
  await expect(page.getByRole('heading', { name: 'No outputs yet' })).toBeVisible()
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  const composer = page.getByRole('textbox', { name: 'Message', exact: true })
  await composer.fill('Still typing')
  state.chats.alpha.outputs = [output()]
  await expect(outputsTab(page)).toContainText('1')
  await expect(page.getByRole('tab', { name: 'Chat', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(composer).toBeFocused()
  expect(state.files).toHaveLength(0)
})

test('committed revisions update live, history stays immutable, selected output stays pinned', async ({ page, context }) => {
  const state = await fixture(context)
  await page.goto('/chats/alpha')
  await outputsTab(page).click()
  const panel = outputPanel(page)
  state.chats.alpha.outputs = [output(2)]
  state.history.unshift(output(2))
  await expect(panel.getByText('Ready · Revision 2 · CADCraft')).toBeVisible()
  await expect.poll(() => state.files.some(file => file.path.includes('/revisions/2/'))).toBe(true)
  state.failHistory = true
  await panel.getByRole('button', { name: 'Load revision history' }).click()
  await expect(panel.getByRole('alert')).toContainText('History unavailable')
  state.failHistory = false
  await panel.getByRole('button', { name: 'Load revision history' }).click()
  await panel.getByRole('combobox', { name: 'Revision', exact: true }).selectOption({ label: 'Revision 1' })
  await expect(panel.getByText('Ready · Revision 1 · CADCraft')).toBeVisible()
  state.chats.alpha.outputs = [output(1, { id: 'c'.repeat(32), title: 'Other document' }), output(3)]
  await expect(outputsTab(page)).toContainText('2')
  await expect(panel.getByRole('heading', { name: 'Mounting bracket' })).toBeVisible()
  await expect(panel.getByText('Ready · Revision 1 · CADCraft')).toBeVisible()
  await panel.getByRole('combobox', { name: 'Revision', exact: true }).selectOption({ label: 'Latest · 3' })
  await expect(panel.getByText('Ready · Revision 3 · CADCraft')).toBeVisible()
})

test('viewing outputs does not acknowledge hidden replies', async ({ page, context }) => {
  const state = await fixture(context)
  await page.goto('/chats/alpha')
  await expect.poll(() => state.reads.some(read => read.message_id === 'reply')).toBe(true)
  await outputsTab(page).click()
  state.chats.alpha.messages.push({ id: 'unread-reply', role: 'assistant', content: 'A new response', meta: {} })
  state.chats.alpha.outputs = [output(2)]
  await expect(outputPanel(page).getByText('Ready · Revision 2 · CADCraft')).toBeVisible()
  expect(state.reads.some(read => read.message_id === 'unread-reply')).toBe(false)
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  await expect(page.getByText('A new response', { exact: true })).toBeVisible()
  await expect.poll(() => state.reads.some(read => read.message_id === 'unread-reply')).toBe(true)
})

test('inline result card opens Outputs and image/download errors are retryable', async ({ page, context }) => {
  const state = await fixture(context, { inline: true })
  state.failPreview = true
  await page.goto('/chats/alpha')
  await page.getByRole('button', { name: 'View output: Mounting bracket', exact: true }).click()
  const panel = outputPanel(page)
  await expect(panel.getByText(/Could not load preview/)).toBeVisible()
  state.failPreview = false
  await panel.getByRole('button', { name: 'Retry preview', exact: true }).click()
  await expect(panel.locator('img')).toBeVisible()
  state.failDownload = true
  await panel.getByRole('button', { name: 'Download bracket.pdf', exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText('Could not download file')
  state.failDownload = false
  const downloaded = page.waitForEvent('download')
  await panel.getByRole('button', { name: 'Download bracket.pdf', exact: true }).click()
  await downloaded
  await expect(panel.getByRole('alert')).toHaveCount(0)
})

test('steered answers do not repeat already-saved outputs in the live response', async ({ page, context }) => {
  const state = await fixture(context, { inline: true })
  state.chats.alpha.active_turn = { id: 'turn', steps: [{ kind: 'text', text: 'Making the requested changes.' }] }
  await page.goto('/chats/alpha')
  const cards = page.getByRole('button', { name: 'View output: Mounting bracket', exact: true })
  await expect(page.getByText('Making the requested changes.', { exact: true })).toBeVisible()
  await expect(cards).toHaveCount(1)
  state.chats.alpha.outputs = [output(2)]
  await expect(cards).toHaveCount(2)
  await expect(page.getByText('Ready · Revision 1 · CADCraft')).toBeVisible()
  await expect(page.getByText('Ready · Revision 2 · CADCraft')).toBeVisible()
})

test('older reply cards open their exact revision and preserve it across tab switches', async ({ page, context }) => {
  const state = await fixture(context, { inline: true })
  state.chats.alpha.outputs = [output(2)]
  state.history.unshift(output(2))
  await page.goto('/chats/alpha')
  await page.getByRole('button', { name: 'View output: Mounting bracket', exact: true }).click()
  const panel = outputPanel(page)
  await expect(panel.getByText('Ready · Revision 1 · CADCraft')).toBeVisible()
  const downloaded = page.waitForEvent('download')
  await panel.getByRole('button', { name: 'Download bracket.pdf', exact: true }).click()
  await downloaded
  expect(state.files.find(file => !file.preview).path).toContain('/revisions/1/')
  state.chats.alpha.outputs = [output(3)]
  state.history.unshift(output(3))
  await expect(panel.getByRole('combobox', { name: 'Revision', exact: true })).toContainText('Latest · 3')
  await page.getByRole('tab', { name: 'Chat', exact: true }).click()
  await outputsTab(page).click()
  await expect(panel.getByText('Ready · Revision 1 · CADCraft')).toBeVisible()
  await panel.getByRole('combobox', { name: 'Revision', exact: true }).selectOption({ label: 'Latest · 3' })
  await expect(panel.getByText('Ready · Revision 3 · CADCraft')).toBeVisible()
})

test('switching chats cancels a pending preview without leaking a late object URL', async ({ page, context }) => {
  const state = await fixture(context)
  let release
  state.waitFile = new Promise(resolve => { release = resolve })
  await context.addInitScript(() => {
    const create = URL.createObjectURL.bind(URL)
    window.outputBlobs = 0
    URL.createObjectURL = (...args) => { window.outputBlobs++; return create(...args) }
  })
  await page.goto('/chats/alpha')
  await outputsTab(page).click()
  await expect.poll(() => state.files.length).toBe(1)
  await page.locator('#shell-sidebar a[href="/chats/beta"]').click()
  await outputsTab(page).click()
  await expect(page.getByRole('heading', { name: 'No outputs yet' })).toBeVisible()
  release()
  await expect.poll(() => state.completedFiles).toBe(1)
  expect(await page.evaluate(() => window.outputBlobs)).toBe(0)
  await expect(outputPanel(page).locator('img')).toHaveCount(0)
  await page.locator('#shell-sidebar a[href="/chats/alpha"]').click()
  await outputsTab(page).click()
  await expect(outputPanel(page).locator('img')).toBeVisible()
  expect(await page.evaluate(() => window.outputBlobs)).toBe(1)
})

test('tab controls support keyboard navigation and route changes discard old outputs', async ({ page, context }) => {
  await fixture(context)
  await page.goto('/chats/alpha')
  const chatTab = page.getByRole('tab', { name: 'Chat', exact: true })
  await chatTab.focus()
  await page.keyboard.press('ArrowRight')
  await expect(outputsTab(page)).toBeFocused()
  await expect(outputPanel(page)).toBeVisible()
  await page.keyboard.press('Home')
  await expect(chatTab).toBeFocused()
  await page.keyboard.press('End')
  await expect(outputsTab(page)).toBeFocused()
  await page.locator('#shell-sidebar a[href="/chats/beta"]').click()
  await expect(chatTab).toHaveAttribute('aria-selected', 'true')
  await outputsTab(page).click()
  await expect(page.getByRole('heading', { name: 'No outputs yet' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Mounting bracket' })).toHaveCount(0)
})
