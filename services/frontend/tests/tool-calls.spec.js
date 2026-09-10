import { test, expect } from '@playwright/test'

async function mockChat (context, data) {
  const chat = { id: 'tools', title: 'Tool calls', model: 'test/model', read_revision: 0 }
  await context.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
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
  kind: 'tool', id, name, ok,
  args: name === 'bash' ? { command: `echo ${id}` } : { path: `src/${id}.js` },
  result: ok === null ? '' : `Output for ${id}`
})
const text = (value) => ({ kind: 'text', text: value })

for (const width of [1440, 320]) {
  test(`tool calls collapse into one summary and retain the existing timeline at ${width}px`, async ({ page, context }) => {
    const steps = [
      text('I will check the project.'),
      ...Array.from({ length: 20 }, (_, index) => tool(`shell-${index}`)),
      text('Now reading the source.'), tool('source', 'read'), tool('source-edit', 'edit'),
      text('The final answer stays visible.')
    ]
    await mockChat(context, { messages: [{ id: 'answer', role: 'assistant', content: '', meta: { steps } }] })
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats/tools')
    const group = page.locator('.tool-group')
    const summary = group.locator(':scope > summary')
    const timeline = group.locator('.tool-group__timeline')
    await expect(group).toHaveCount(1)
    await expect(summary.locator('.tool-group__label')).toHaveText('Ran 20 shell commands and 2 other tool calls')
    await expect(timeline).toBeHidden()
    await expect(page.getByText('I will check the project.')).toBeVisible()
    await expect(page.getByText('The final answer stays visible.')).toBeVisible()
    await expect(page.getByText('Now reading the source.')).toBeHidden()
    await expect(group.getByLabel('Tool calls in progress')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `test-results/tool-summary-${width}.png` })

    // Collapsing narration must not omit it from the Markdown response copy.
    await page.getByRole('button', { name: 'Copy response', exact: true }).click()
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('I will check the project.\n\nNow reading the source.\n\nThe final answer stays visible.')
    await summary.focus()
    await page.keyboard.press('Enter')
    await expect(timeline).toBeVisible()
    await expect(timeline.locator('.tool')).toHaveCount(22)
    await expect(timeline.getByText('Now reading the source.')).toBeVisible()
    expect(await timeline.locator(':scope > *').evaluateAll((elements) => elements.map((el) => el.className))).toEqual([
      ...Array(20).fill('tool'), 'bubble__body', 'tool', 'tool'
    ])
    const call = timeline.locator('.tool').first()
    await call.locator('.tool__row').click()
    await expect(call.locator('.tool__panel')).toContainText('echo shell-0')
    await expect(call.locator('.tool__panel')).toContainText('Output for shell-0')
    await summary.focus()
    await page.keyboard.press('Space')
    await expect(timeline).toBeHidden()
    await summary.click()
    await expect(call.locator('.tool__panel')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.reload()
    await expect(summary).toContainText('Ran 20 shell commands and 2 other tool calls')
    await expect(timeline).toBeHidden()
  })
}

test('live counts update while collapsed and expanded without resetting open calls', async ({ page, context }) => {
  const data = { messages: [], active_turn: { id: 'active', steps: [text('Working on it.')] } }
  await mockChat(context, data)
  await page.goto('/chats/tools')
  const group = page.locator('.tool-group')
  await expect(page.getByText('Working on it.')).toBeVisible()
  await expect(group).toHaveCount(0)
  data.active_turn.steps.push(tool('one', 'bash', null))
  const summary = group.locator(':scope > summary')
  const timeline = group.locator('.tool-group__timeline')
  await expect(summary).toContainText('Ran 1 shell command')
  await expect(timeline).toBeHidden()
  await expect(group.getByLabel('Tool calls in progress')).toBeVisible()
  data.active_turn.steps[1].ok = true
  data.active_turn.steps[1].result = 'First command complete'
  data.active_turn.steps.push(text('Checking the next file.'), tool('two', 'read', null))
  await expect(summary).toContainText('Ran 1 shell command and 1 other tool call')
  await expect(timeline).toBeHidden()
  await summary.click()
  const call = timeline.locator('.tool').first()
  await call.locator('.tool__row').click()
  await expect(call.locator('.tool__panel')).toContainText('First command complete')
  data.active_turn.steps[3].ok = false
  data.active_turn.steps[3].result = 'Read failed'
  data.active_turn.steps.push(tool('three', 'bash', null))
  await expect(summary).toContainText('Ran 2 shell commands and 1 other tool call')
  await expect(summary).toContainText('1 failed')
  await expect(timeline).toBeVisible()
  await expect(call.locator('.tool__panel')).toBeVisible()
  await expect(timeline.locator('.tool')).toHaveCount(3)
  await timeline.locator('.tool--bad .tool__row').click()
  await expect(timeline.locator('.tool--bad .tool__panel')).toContainText('Read failed')
  await summary.click()
  data.active_turn.steps[4].ok = true
  data.active_turn.steps.push(text('Finished with a read error.'))
  await expect(group.getByLabel('Tool calls in progress')).toHaveCount(0)
  await expect(summary).toContainText('1 failed')
  await expect(page.getByText('Finished with a read error.')).toBeVisible()
  data.messages = [{ id: 'saved', role: 'assistant', content: 'Finished with a read error.', meta: { steps: data.active_turn.steps } }]
  data.active_turn = null
  await expect(page.getByRole('button', { name: 'Stop response', exact: true })).toHaveCount(0)
  await expect(summary).toContainText('Ran 2 shell commands and 1 other tool call')
  await expect(timeline).toBeHidden()
})

test('legacy tool names are counted and messages expand independently', async ({ page, context }) => {
  await mockChat(context, { messages: [
    { id: 'plain', role: 'assistant', content: 'Plain response', meta: {} },
    { id: 'legacy', role: 'assistant', content: 'Legacy response', meta: { tools: ['read', 'read', 'custom_tool'] } },
    { id: 'new', role: 'assistant', content: 'New response', meta: { steps: [tool('only', 'bash')] } }
  ] })
  await page.goto('/chats/tools')
  const groups = page.locator('.tool-group')
  await expect(groups).toHaveCount(2)
  await expect(groups.first().locator('summary')).toContainText('Ran 3 tool calls')
  await expect(groups.last().locator('summary')).toContainText('Ran 1 shell command')
  await expect(page.getByText('Plain response')).toBeVisible()
  await expect(page.getByText('Legacy response')).toBeVisible()
  await expect(page.getByText('New response')).toBeVisible()
  await expect(page.getByLabel('Tool calls in progress')).toHaveCount(0)
  await groups.first().locator('summary').click()
  await expect(groups.first().locator('.tool')).toHaveCount(3)
  await expect(groups.first().locator('.tool-group__timeline')).toBeVisible()
  await expect(groups.last().locator('.tool-group__timeline')).toBeHidden()
})
