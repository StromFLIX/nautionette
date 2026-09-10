import { test, expect } from '@playwright/test'

async function mockChat (context, data) {
  const chat = { id: 'tools', title: 'Tool calls', model: 'test/model', read_revision: 0 }
  await context.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    chat.answering = Boolean(data.active_turn)
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/chats/tools/stream') return route.fulfill({ contentType: 'text/event-stream', body: `retry: 100\ndata: ${JSON.stringify({ type: 'snapshot', chat, ...data })}\n\n` })
    if (path === '/api/chats/tools/read-state') return route.fulfill({ json: chat })
    if (path === '/api/chats/tools') return route.fulfill({ json: { chat, ...data } })
    if (path === '/api/chats') return route.fulfill({ json: { chats: [chat] } })
    if (path === '/api/catalog') return route.fulfill({ json: { models: [], tools: [], agent_sets: [], default_model: 'test/model' } })
    if (path === '/api/system') return route.fulfill({ json: { components: [], agent_sets: [] } })
    return route.fulfill({ json: { workflows: [], drafts: [], runs: [] } })
  })
}

const tool = (id, name = 'bash', ok = true) => ({
  kind: 'tool', id, name, ok, args: {}, result: ok === null ? '' : `Output for ${id}`
})
const text = (value) => ({ kind: 'text', text: value })

for (const width of [1440, 320]) {
  test(`expanded timing is concise, persisted and not visible when collapsed at ${width}px`, async ({ page, context }) => {
    const steps = [
      { ...tool('slow'), duration_ms: 12_300 },
      text('Now checking a file.'),
      { ...tool('failed', 'read', false), duration_ms: 170 }
    ]
    const timing = { tools_ms: 12_470, thinking_ms: 3200, reply_ms: 800, other_ms: 250, active: null }
    await mockChat(context, { messages: [{ id: 'saved', role: 'assistant', content: 'Done.', meta: { steps, timing } }] })
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats/tools')
    const group = page.locator('.tool-group')
    const breakdown = group.getByRole('group', { name: 'Response timing' })
    await expect(breakdown).toHaveCount(0)
    await group.locator('summary').click()
    await expect(breakdown).toHaveText('Tools 12.5sThinking 3.2sReply 800msOther 250ms')
    await expect(breakdown).toHaveCSS('font-size', '13.75px')
    await expect(group.locator('.tool__duration')).toHaveText(['12.3s', '170ms'])
    await expect(group.locator('.tool__pulse')).toHaveCount(0)
    await expect(group.locator('.tool-group__indicator-arc')).toHaveCount(0)
    await expect(group.locator('.tool-group__failed')).toHaveText('1 failed')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `test-results/tool-timing-${width}.png` })
    await group.locator('summary').click()
    await expect(breakdown).toHaveCount(0)
    await page.reload()
    await group.locator('summary').click()
    await expect(breakdown).toHaveText('Tools 12.5sThinking 3.2sReply 800msOther 250ms')
  })
}

test('dots track individual execution, not model activity; live timings freeze on completion', async ({ page, context }) => {
  const data = { messages: [], active_turn: { id: 'active', steps: [
    tool('one', 'bash', null), tool('two', 'read', null)
  ], timing: { tools_ms: 1000, thinking_ms: 500, reply_ms: 0, other_ms: 200,
    active: 'tools', updated_at: Date.now() / 1000 } } }
  await mockChat(context, data)
  await page.goto('/chats/tools')
  const group = page.locator('.tool-group')
  await group.locator('summary').click()
  const breakdown = group.getByRole('group', { name: 'Response timing' })
  const dots = page.getByLabel('Tool running', { exact: true })
  await expect(dots).toHaveCount(2)
  const initial = await breakdown.textContent()
  await expect.poll(() => breakdown.textContent()).not.toBe(initial)
  data.active_turn.steps[0].ok = true
  data.active_turn.steps[0].duration_ms = 1500
  await expect(dots).toHaveCount(1)
  await expect(group.locator('.tool__duration')).toHaveText('1.5s')
  await expect(group.getByLabel('Tool calls in progress')).toBeVisible()
  // An incomplete/interrupted result is not a running tool, even in a live answer.
  Object.assign(data.active_turn.steps[1], { interrupted: true, finished_at: Date.now() / 1000, duration_ms: 2000 })
  Object.assign(data.active_turn.timing, { tools_ms: 2000, active: 'thinking', updated_at: Date.now() / 1000 })
  await expect(dots).toHaveCount(0)
  await expect(group.getByLabel('Tool calls in progress')).toHaveCount(0)
  await expect(page.locator('.composer')).toHaveClass(/composer--running/)
  await expect(breakdown).toContainText('Tools 2s')
  await group.locator('.tool__row').last().click()
  await expect(group.locator('.tool__panel')).toContainText('Interrupted.')
  data.active_turn.timing = { tools_ms: 2000, thinking_ms: 1000, reply_ms: 500, other_ms: 200, active: null }
  data.messages = [{ id: 'saved', role: 'assistant', content: 'Done.', meta: {
    steps: data.active_turn.steps, timing: data.active_turn.timing
  } }]
  data.active_turn = null
  await expect(page.getByRole('button', { name: 'Stop response', exact: true })).toHaveCount(0)
  await group.locator('summary').click()
  await expect(breakdown).toHaveText('Tools 2sThinking 1sReply 500msOther 200ms')
  await page.waitForTimeout(400)
  await expect(breakdown).toHaveText('Tools 2sThinking 1sReply 500msOther 200ms')
  await expect(dots).toHaveCount(0)
})

test('restart timing is labeled as the recorded portion rather than inventing downtime', async ({ page, context }) => {
  await mockChat(context, { messages: [{ id: 'interrupted', role: 'assistant', content: 'Interrupted', meta: {
    steps: [tool('old', 'bash', null)],
    timing: { tools_ms: 2200, thinking_ms: 1000, active: null, partial: true, updated_at: 1 }
  } }] })
  await page.goto('/chats/tools')
  await page.locator('.tool-group summary').click()
  await expect(page.getByRole('group', { name: 'Response timing' })).toHaveText('RecordedTools 2.2sThinking 1s')
  await expect(page.locator('.tool__pulse, .tool__duration, .tool-group__indicator-arc')).toHaveCount(0)
})
