import { test, expect } from '@playwright/test'

const notice = { kind: 'recovery', attempt: 1, max_attempts: 2,
  message: 'Automatic recovery 1/2: interrupted model connection' }
const initialSteps = () => [
  { kind: 'text', text: 'The edit was saved.' },
  { kind: 'tool', id: 'edit', name: 'write', args: { path: 'kept.txt' }, ok: true, result: 'Saved' },
  { ...notice }
]

async function mockRecovery (context) {
  const chat = { id: 'recover', title: 'Recovering work', model: 'test/model', read_revision: 0 }
  const data = { messages: [{ id: 'turn', role: 'user', content: 'Do the work', meta: {} }],
    active_turn: { id: 'turn', steps: initialSteps(), status: 'Recovering automatically in 2s (1/2); keeping completed work' } }
  const posts = []
  const finish = (error = null) => {
    data.messages.push({ id: 'answer', role: 'assistant', content: data.active_turn.steps
      .filter(step => step.kind === 'text').map(step => step.text).join(''),
    meta: { steps: structuredClone(data.active_turn.steps), error } })
    data.active_turn = null
  }
  await context.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() === 'POST') posts.push({ path, body: route.request().postDataJSON() })
    chat.answering = Boolean(data.active_turn)
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/chats/recover/stream') return route.fulfill({ contentType: 'text/event-stream', body: `retry: 100\ndata: ${JSON.stringify({ type: 'snapshot', chat, ...data })}\n\n` })
    if (path === '/api/chats/recover/stop') {
      finish('Stopped by you.')
      chat.answering = false
      chat.queue_paused = 1
      return route.fulfill({ json: { chat, ...data } })
    }
    if (path === '/api/chats/recover/read-state') return route.fulfill({ json: chat })
    if (path === '/api/chats/recover') return route.fulfill({ json: { chat, ...data } })
    if (path === '/api/chats') return route.fulfill({ json: { chats: [chat] } })
    if (path === '/api/catalog') return route.fulfill({ json: { models: [], tools: [], agent_sets: [], default_model: 'test/model' } })
    if (path === '/api/system') return route.fulfill({ json: { components: [], agent_sets: [] } })
    return route.fulfill({ json: { workflows: [], drafts: [], runs: [] } })
  })
  return { data, posts, finish }
}

for (const width of [1440, 320]) test(`recovery is visible live and after reload without fake user messages at ${width}px`, async ({ page, context }) => {
  const { data, finish } = await mockRecovery(context)
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(error.message))
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.setViewportSize({ width, height: 900 })
  await page.goto('/chats/recover')
  await expect(page.getByRole('status').filter({ hasText: notice.message })).toBeVisible()
  await expect(page.locator('.bubble__status')).toContainText('Recovering automatically')
  await expect(page.getByRole('button', { name: 'Stop response', exact: true })).toBeVisible()
  await expect(page.locator('.msg--user')).toHaveCount(1)
  data.active_turn.steps.push({ kind: 'text', text: 'Continued without repeating it.' })
  data.active_turn.status = ''
  finish()
  await expect(page.getByRole('button', { name: 'Stop response', exact: true })).toHaveCount(0)
  await expect(page.getByText('Continued without repeating it.', { exact: true })).toBeVisible()
  await expect(page.locator('.bubble__recovery')).toHaveCount(1)
  await expect(page.locator('.bubble__error')).toHaveCount(0)
  await expect(page.locator('.tool-group')).toHaveCount(1)
  await page.getByRole('button', { name: 'Copy response', exact: true }).click()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('The edit was saved.\n\nContinued without repeating it.')
  await page.reload()
  await expect(page.getByRole('status').filter({ hasText: notice.message })).toBeVisible()
  await expect(page.getByText('Continued without repeating it.', { exact: true })).toBeVisible()
  await expect(page.locator('.msg--user')).toHaveCount(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(pageErrors).toEqual([])
})

test('Stop remains available during recovery and targets the original turn', async ({ page, context }) => {
  const { posts } = await mockRecovery(context)
  await page.goto('/chats/recover')
  await expect(page.locator('.bubble__status')).toContainText('Recovering automatically')
  await page.getByRole('button', { name: 'Stop response', exact: true }).click()
  await expect(page.getByText('Stopped by you.', { exact: true })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: notice.message })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop response', exact: true })).toHaveCount(0)
  expect(posts.filter(post => post.path.endsWith('/stop'))).toEqual([{ path: '/api/chats/recover/stop', body: { turn_id: 'turn' } }])
  expect(posts.some(post => post.path.endsWith('/messages'))).toBe(false)
  await expect(page.locator('.msg--user')).toHaveCount(1)
})
