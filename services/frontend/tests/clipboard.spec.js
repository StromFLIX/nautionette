import { test, expect } from '@playwright/test'

async function mockChat (context, data) {
  const chat = { id: 'copy', title: 'Copy test', model: 'test/model', read_revision: 0 }
  await context.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/chats/copy/stream') return route.fulfill({ contentType: 'text/event-stream', body: `retry: 100\ndata: ${JSON.stringify({ type: 'snapshot', chat, ...data })}\n\n` })
    if (path === '/api/chats/copy/read-state') return route.fulfill({ json: chat })
    if (path === '/api/chats/copy') return route.fulfill({ json: { chat, ...data } })
    if (path === '/api/chats') return route.fulfill({ json: { chats: [chat] } })
    if (path === '/api/catalog') return route.fulfill({ json: { models: [], tools: [], agent_sets: [], default_model: 'test/model' } })
    if (path === '/api/system') return route.fulfill({ json: { components: [], agent_sets: [] } })
    return route.fulfill({ json: { workflows: [], drafts: [], runs: [] } })
  })
}

const clipboard = (page) => page.evaluate(() => navigator.clipboard.readText())

for (const width of [1440, 320]) {
  test(`code blocks and whole responses can always be copied at ${width}px`, async ({ page, context }) => {
    const code = 'const html = "<div>& hello</div>";\n  console.log(html);' + ' // long'.repeat(80)
    const intro = `Before **running**:\n\n\`\`\`javascript\n${code}\n\`\`\``
    const ending = 'Afterwards:\n\n~~~\n  unlabelled & <raw>\n~~~\n\n```unknown-language\nlast block\n```'
    await mockChat(context, { messages: [{
      id: 'answer', role: 'assistant', content: 'Do not copy this stale summary',
      meta: { steps: [
        { kind: 'text', text: intro },
        { kind: 'tool', name: 'test', args: {}, ok: true, result: 'Do not copy tool output' },
        { kind: 'text', text: ending }
      ] }
    }] })
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats/copy')
    const message = page.locator('.msg--assistant')
    const buttons = message.getByRole('button', { name: 'Copy code', exact: true })
    await expect(buttons).toHaveCount(3)
    for (const [index, expected] of [code, '  unlabelled & <raw>', 'last block'].entries()) {
      await expect(buttons.nth(index)).toBeVisible()
      await buttons.nth(index).click()
      await expect(message.locator('.bubble__copy-status')).toHaveText('Code copied')
      expect(await clipboard(page)).toBe(expected)
    }
    await message.locator('pre').first().evaluate((element) => { element.scrollLeft = element.scrollWidth })
    await buttons.first().focus()
    await page.keyboard.press('Enter')
    expect(await clipboard(page)).toBe(code)
    await message.getByRole('button', { name: 'Copy response', exact: true }).click()
    await expect(message.locator('.bubble__copy-status')).toHaveText('Response copied')
    expect(await clipboard(page)).toBe(`${intro}\n\n${ending}`)
    expect(await message.locator('.bubble__body').first().evaluate((el) => getComputedStyle(el).userSelect)).toBe('text')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.reload()
    await expect(buttons).toHaveCount(3)
    await expect(message.getByRole('button', { name: 'Copy response', exact: true })).toBeVisible()
  })
}

test('copy controls track streaming text and remain available on saved plain responses', async ({ page, context }) => {
  const data = { messages: [], active_turn: { id: 'active', steps: [{ kind: 'text', text: 'Starting\n\n```python\nprint(1)' }] } }
  await mockChat(context, data)
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto('/chats/copy')
  const message = page.locator('.msg--assistant')
  const codeButton = message.getByRole('button', { name: 'Copy code', exact: true })
  await codeButton.click()
  expect(await clipboard(page)).toBe('print(1)')
  data.active_turn.steps[0].text = 'Starting\n\n```python\nprint(1)\nprint(2)\n```\n\nDone'
  await expect(message).toContainText('print(2)')
  await codeButton.click()
  expect(await clipboard(page)).toBe('print(1)\nprint(2)')
  await message.getByRole('button', { name: 'Copy response', exact: true }).click()
  expect(await clipboard(page)).toBe(data.active_turn.steps[0].text)
  data.active_turn = null
  data.messages = [{ id: 'done', role: 'assistant', content: 'Plain **final** response', meta: {} }]
  await expect(message).toContainText('Plain final response')
  await message.getByRole('button', { name: 'Copy response', exact: true }).click()
  expect(await clipboard(page)).toBe('Plain **final** response')
})

test('copy falls back without the Clipboard API and reports denied clipboard access honestly', async ({ page, context }) => {
  const content = 'Copy me\n\n```\nhello\n```'
  await mockChat(context, { messages: [{ id: 'answer', role: 'assistant', content, meta: {} }] })
  await page.goto('/chats/copy')
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    document.execCommand = (command) => {
      window.copied = { command, text: document.activeElement.value }
      return true
    }
  })
  const button = page.getByRole('button', { name: 'Copy response', exact: true })
  await button.click()
  await expect(page.locator('.bubble__copy-status')).toHaveText('Response copied')
  expect(await page.evaluate(() => window.copied)).toEqual({ command: 'copy', text: content })
  await expect(button).toBeFocused()
  await expect(page.locator('body > textarea')).toHaveCount(0)
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw new Error('Denied') } } })
  })
  await page.getByRole('button', { name: 'Copy code', exact: true }).click()
  expect(await page.evaluate(() => window.copied.text)).toBe('hello')
  await page.evaluate(() => { document.execCommand = () => false })
  await button.click()
  await expect(page.locator('.bubble__copy-status')).toContainText('Could not copy')
  await expect(button).toBeEnabled()
  await expect(page.locator('body > textarea')).toHaveCount(0)
})

test('markdown copy controls do not allow model-authored buttons or executable HTML', async ({ page, context }) => {
  await mockChat(context, { messages: [{
    id: 'unsafe', role: 'assistant',
    content: '<button class="code-block__copy" onclick="window.unsafe = true">Fake</button>\n\n<img src=x onerror="window.unsafe = true">\n\n<script>window.unsafe = true</script>\n\n```html\n<script>alert("hello")</script>\n```', meta: {}
  }] })
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto('/chats/copy')
  await expect(page.locator('.bubble__body button')).toHaveCount(1)
  await expect(page.locator('.bubble__body script, .bubble__body img, .bubble__body [onclick]')).toHaveCount(0)
  await page.getByRole('button', { name: 'Copy code', exact: true }).click()
  expect(await clipboard(page)).toBe('<script>alert("hello")</script>')
  expect(await page.evaluate(() => window.unsafe)).toBeUndefined()
})
