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
  kind: 'tool', id, name, ok,
  args: name === 'bash' ? { command: `echo ${id}` } : { path: `src/${id}.js` },
  result: ok === null ? '' : `Output for ${id}`
})
const text = (value) => ({ kind: 'text', text: value })

async function expectSummaryAlignment (summary) {
  const label = summary.locator('.tool-group__label')
  const labelBox = await label.boundingBox()
  const lineHeight = await label.evaluate(el => parseFloat(getComputedStyle(el).lineHeight))
  const firstLineCenter = labelBox.y + lineHeight / 2
  for (const selector of ['.tool-group__indicator', '.tool-group__chevron']) {
    const box = await summary.locator(selector).boundingBox()
    expect(Math.abs(box.y + box.height / 2 - firstLineCenter)).toBeLessThan(1)
  }
}

for (const width of [1440, 320]) {
  test(`tool groups collapse independently with narration visible between them at ${width}px`, async ({ page, context }) => {
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
    const groups = page.locator('.tool-group')
    const group = groups.first()
    const summary = group.locator(':scope > summary')
    const timeline = group.locator('.tool-group__timeline')
    const nextGroup = groups.nth(1)
    const nextTimeline = nextGroup.locator('.tool-group__timeline')
    const narration = page.getByText('Now reading the source.')
    await expect(groups).toHaveCount(2)
    await expect(summary.locator('.tool-group__label')).toHaveText('Ran 20 shell commands')
    await expect(nextGroup.locator('.tool-group__label')).toHaveText('Ran 2 tool calls')
    await expect(nextTimeline).toBeHidden()
    await expect(timeline).toBeHidden()
    await expect(page.getByText('I will check the project.')).toBeVisible()
    await expect(page.getByText('The final answer stays visible.')).toBeVisible()
    await expect(narration).toBeVisible()
    await expect(groups.locator('.bubble__body')).toHaveCount(0)
    expect(await page.locator('.msg--assistant .bubble > .bubble__body, .msg--assistant .bubble > .tool-group')
      .evaluateAll(elements => elements.map(el => el.className))).toEqual([
      'bubble__body', 'tool-group', 'bubble__body', 'tool-group', 'bubble__body'
    ])
    await expect(group.getByLabel('Tool calls in progress')).toHaveCount(0)
    const indicator = summary.locator('.tool-group__indicator')
    await expect(indicator).toBeVisible()
    await expect(indicator).toHaveAttribute('aria-hidden', 'true')
    await expect(indicator.locator('.tool-group__indicator-arc, .tool-group__indicator-dot')).toHaveCount(0)
    await expectSummaryAlignment(summary)
    const iconBox = await indicator.boundingBox()
    const labelBox = await summary.locator('.tool-group__label').boundingBox()
    expect(iconBox.width).toBeLessThanOrEqual(16)
    expect(iconBox.x + iconBox.width).toBeLessThan(labelBox.x)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `test-results/tool-summary-${width}.png` })

    // Copy still includes all narration, regardless of disclosure state.
    await page.getByRole('button', { name: 'Copy response', exact: true }).click()
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('I will check the project.\n\nNow reading the source.\n\nThe final answer stays visible.')
    await summary.focus()
    await page.keyboard.press('Enter')
    await expect(timeline).toBeVisible()
    await expect(timeline.locator('.tool')).toHaveCount(20)
    await expect(nextTimeline).toBeHidden()
    await expect(narration).toBeVisible()
    await nextGroup.locator(':scope > summary').click()
    await expect(nextTimeline).toBeVisible()
    await expect(nextTimeline.locator('.tool')).toHaveCount(2)
    await expectSummaryAlignment(nextGroup.locator(':scope > summary'))
    const call = timeline.locator('.tool').first()
    await call.locator('.tool__row').click()
    await expect(call.locator('.tool__panel')).toContainText('echo shell-0')
    await expect(call.locator('.tool__panel')).toContainText('Output for shell-0')
    await summary.focus()
    await page.keyboard.press('Space')
    await expect(timeline).toBeHidden()
    await expect(nextTimeline).toBeVisible()
    await expect(narration).toBeVisible()
    await summary.click()
    await expect(call.locator('.tool__panel')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.reload()
    await expect(groups).toHaveCount(2)
    await expect(summary).toContainText('Ran 20 shell commands')
    await expect(nextGroup.locator('.tool-group__label')).toHaveText('Ran 2 tool calls')
    await expect(timeline).toBeHidden()
    await expect(nextTimeline).toBeHidden()
    await expect(narration).toBeVisible()
  })
}

test('consecutive live counts update while collapsed and expanded without resetting open calls', async ({ page, context }) => {
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
  const arc = group.locator('.tool-group__indicator-arc')
  const dot = group.locator('.tool-group__indicator-dot')
  await expect(dot).toBeVisible()
  await expect(dot).toHaveCSS('animation-name', /tool-group-pulse/)
  const opacity = await dot.evaluate(el => getComputedStyle(el).opacity)
  await expect.poll(() => dot.evaluate(el => getComputedStyle(el).opacity)).not.toBe(opacity)
  await expect(arc).toHaveCSS('animation-name', /tool-group-orbit/)
  const orbitNode = await arc.elementHandle()
  const offset = await arc.evaluate(el => getComputedStyle(el).strokeDashoffset)
  await expect.poll(() => arc.evaluate(el => getComputedStyle(el).strokeDashoffset)).not.toBe(offset)
  // Only the light travels; the octagonal outline stays upright.
  await expect(group.locator('.tool-group__indicator')).toHaveCSS('transform', 'none')
  data.active_turn.steps[1].ok = true
  data.active_turn.steps[1].result = 'First command complete'
  // Thinking between calls must not stop or remount the orbit.
  await expect(dot).toHaveCount(0)
  await expect(group.getByLabel('Response in progress')).toBeVisible()
  await expect(arc).toHaveCSS('animation-name', /tool-group-orbit/)
  expect(await arc.evaluate((el, original) => el === original, orbitNode)).toBe(true)
  const thinkingOffset = await arc.evaluate(el => getComputedStyle(el).strokeDashoffset)
  await expect.poll(() => arc.evaluate(el => getComputedStyle(el).strokeDashoffset)).not.toBe(thinkingOffset)
  data.active_turn.steps.push(tool('two', 'read', null))
  await expect(summary).toContainText('Ran 1 shell command and 1 other tool call')
  await expect(dot).toBeVisible()
  await expect(group.getByLabel('Tool calls in progress')).toBeVisible()
  expect(await arc.evaluate((el, original) => el === original, orbitNode)).toBe(true)
  await expect(timeline).toBeHidden()
  await summary.click()
  const call = timeline.locator('.tool').first()
  await call.locator('.tool__row').click()
  await expect(call.locator('.tool__panel')).toContainText('First command complete')
  data.active_turn.steps[2].ok = false
  data.active_turn.steps[2].result = 'Read failed'
  data.active_turn.steps.push(tool('three', 'bash', null))
  await expect(summary).toContainText('Ran 2 shell commands and 1 other tool call')
  await expect(summary).toContainText('1 failed')
  await expect(timeline).toBeVisible()
  await expect(call.locator('.tool__panel')).toBeVisible()
  await expect(timeline.locator('.tool')).toHaveCount(3)
  await timeline.locator('.tool--bad .tool__row').click()
  await expect(timeline.locator('.tool--bad .tool__panel')).toContainText('Read failed')
  await summary.click()
  data.active_turn.steps[3].ok = true
  data.active_turn.steps.push(text('Finished with a read error.'))
  await expect(group.getByLabel('Tool calls in progress')).toHaveCount(0)
  await expect(dot).toHaveCount(0)
  // The model is still streaming its final answer after the last tool finishes.
  await expect(group.getByLabel('Response in progress')).toBeVisible()
  await expect(arc).toHaveCSS('animation-name', /tool-group-orbit/)
  expect(await arc.evaluate((el, original) => el === original, orbitNode)).toBe(true)
  await expect(group.locator('.tool-group__indicator-track')).toBeVisible()
  await expect(summary).toContainText('1 failed')
  await expect(page.getByText('Finished with a read error.')).toBeVisible()
  data.messages = [{ id: 'saved', role: 'assistant', content: 'Finished with a read error.', meta: { steps: data.active_turn.steps } }]
  data.active_turn = null
  await expect(page.getByRole('button', { name: 'Stop response', exact: true })).toHaveCount(0)
  await expect(summary).toContainText('Ran 2 shell commands and 1 other tool call')
  await expect(arc).toHaveCount(0)
  await expect(dot).toHaveCount(0)
  await expect(group.locator('.tool-group__indicator')).toHaveAttribute('aria-hidden', 'true')
  await expect(timeline).toBeHidden()
})

test('live narration starts a new group without hiding text or resetting earlier disclosures', async ({ page, context }) => {
  const data = { messages: [], active_turn: { id: 'active', steps: [tool('one', 'bash', null)] } }
  await mockChat(context, data)
  await page.goto('/chats/tools')
  const groups = page.locator('.tool-group')
  const first = groups.first()
  const firstSummary = first.locator(':scope > summary')
  await expect(groups).toHaveCount(1)
  await firstSummary.click()
  const call = first.locator('.tool').first()
  await call.locator('.tool__row').click()
  const firstNode = await first.elementHandle()
  const callNode = await call.elementHandle()

  data.active_turn.steps[0].ok = true
  data.active_turn.steps[0].result = 'First command complete'
  data.active_turn.steps.push(text('Checking **the next file**.'))
  const narration = page.locator('.bubble__body').filter({ hasText: 'Checking the next file.' })
  await expect(narration).toBeVisible()
  await expect(narration.locator('strong')).toHaveText('the next file')
  const narrationNode = await narration.elementHandle()
  await expect(call.locator('.tool__panel')).toContainText('First command complete')
  await expect(first.getByLabel('Response in progress')).toBeVisible()

  data.active_turn.steps.push(tool('two', 'read', null))
  const second = groups.nth(1)
  const secondSummary = second.locator(':scope > summary')
  const secondTimeline = second.locator('.tool-group__timeline')
  await expect(groups).toHaveCount(2)
  await expect(firstSummary).toContainText('Ran 1 shell command')
  await expect(secondSummary).toContainText('Ran 1 tool call')
  await expect(first.locator('.tool-group__indicator-dot')).toHaveCount(0)
  await expect(second.getByLabel('Tool calls in progress')).toBeVisible()
  await expect(secondTimeline).toBeHidden()
  await expect(narration).toBeVisible()
  expect(await narration.evaluate((el, original) => el === original, narrationNode)).toBe(true)
  expect(await first.evaluate((el, original) => el === original, firstNode)).toBe(true)
  expect(await call.evaluate((el, original) => el === original, callNode)).toBe(true)
  await expect(call.locator('.tool__panel')).toBeVisible()
  await expect(groups.locator('.bubble__body')).toHaveCount(0)

  await secondSummary.click()
  await second.locator('.tool__row').click()
  data.active_turn.steps[2].ok = false
  data.active_turn.steps[2].result = 'Read failed'
  data.active_turn.steps.push(tool('three', 'read', null))
  await expect(secondSummary).toContainText('Ran 2 tool calls')
  await expect(secondSummary).toContainText('1 failed')
  await expect(firstSummary).not.toContainText('failed')
  await expect(second.locator('.tool__panel').first()).toContainText('Read failed')
  await expect(secondTimeline).toBeVisible()
  await expect(call.locator('.tool__panel')).toBeVisible()
  await firstSummary.click()
  await expect(first.locator('.tool-group__timeline')).toBeHidden()
  await expect(secondTimeline).toBeVisible()
  await expect(narration).toBeVisible()

  data.active_turn.steps[3].ok = true
  data.active_turn.steps.push(text('Finished.'))
  data.messages = [{ id: 'saved', role: 'assistant', content: '', meta: { steps: data.active_turn.steps } }]
  data.active_turn = null
  await expect(page.getByRole('button', { name: 'Stop response', exact: true })).toHaveCount(0)
  await expect(groups).toHaveCount(2)
  await expect(groups.locator('.tool-group__indicator-arc, .tool-group__indicator-dot')).toHaveCount(0)
  await expect(narration).toBeVisible()
  await expect(page.getByText('Finished.', { exact: true })).toBeVisible()
  await page.reload()
  await expect(groups).toHaveCount(2)
  await expect(first.locator('.tool-group__timeline')).toBeHidden()
  await expect(secondTimeline).toBeHidden()
  await expect(narration).toBeVisible()
  await expect(firstSummary).toContainText('Ran 1 shell command')
  await expect(secondSummary).toContainText('Ran 2 tool calls')
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
  await expectSummaryAlignment(groups.first().locator('summary'))
  await expect(page.getByText('Plain response')).toBeVisible()
  await expect(page.getByText('Legacy response')).toBeVisible()
  await expect(page.getByText('New response')).toBeVisible()
  await expect(page.getByLabel('Tool calls in progress')).toHaveCount(0)
  await groups.first().locator('summary').click()
  await expect(groups.first().locator('.tool')).toHaveCount(3)
  await expect(groups.first().locator('.tool-group__timeline')).toBeVisible()
  await expect(groups.last().locator('.tool-group__timeline')).toBeHidden()
  await expect(page.locator('.activity-timing, .tool__duration, .tool__pulse')).toHaveCount(0)
})

for (const width of [1440, 320]) {
  test(`activity animations can be controlled independently at ${width}px`, async ({ page, context }) => {
    await mockChat(context, { messages: [], active_turn: { id: 'active', steps: [
      text('Checking the project.'),
      ...Array.from({ length: 6 }, (_, i) => tool(`shell-${i}`)),
      ...Array.from({ length: 14 }, (_, i) => tool(`read-${i}`, 'read')),
      tool('failed', 'read', false), tool('pending', 'read', null)
    ] } })
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.goto('/chats/tools')
    const spinner = page.locator('a[href="/chats/tools"] .avatar__spinner')
    const composer = page.locator('.composer')
    const indicator = page.getByLabel('Tool calls in progress')
    const arc = indicator.locator('.tool-group__indicator-arc')
    const dot = indicator.locator('.tool-group__indicator-dot')
    const summary = page.locator('.tool-group__summary')
    const label = summary.locator('.tool-group__label')
    await expect(label).toHaveText('Ran 6 shell commands and 16 other tool calls')
    await expect(summary).toContainText('1 failed')
    await expectSummaryAlignment(summary)
    const iconBox = await indicator.boundingBox()
    const dotBox = await dot.boundingBox()
    expect(Math.abs(dotBox.x + dotBox.width / 2 - (iconBox.x + iconBox.width / 2))).toBeLessThan(1)
    expect(Math.abs(dotBox.y + dotBox.height / 2 - (iconBox.y + iconBox.height / 2))).toBeLessThan(1)
    const labelBox = await label.boundingBox()
    expect(iconBox.x + iconBox.width).toBeLessThan(labelBox.x)
    expect(iconBox.width).toBeLessThanOrEqual(16)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)

    const expectAnimations = async ([chatList, messageWindow, toolIndicator]) => {
      await expect(spinner).toHaveCSS('animation-name', chatList ? 'chat-activity-orbit' : 'none')
      await expect(spinner.locator('.avatar__spinner-arc')).toHaveCSS('animation-name', chatList ? 'chat-activity-sweep' : 'none')
      await expect.poll(() => composer.evaluate(el => getComputedStyle(el, '::before').animationName !== 'none')).toBe(messageWindow)
      await expect(arc).toHaveCSS('animation-name', toolIndicator ? /tool-group-orbit/ : 'none')
      await expect(dot).toHaveCSS('animation-name', toolIndicator ? /tool-group-pulse/ : 'none')
      await expect(dot).toBeVisible()
      await expect(page.getByLabel('Tool running', { exact: true })).toHaveCount(1)
      await expect(page.locator('.tool__pulse')).toHaveCSS('animation-name', toolIndicator ? /tool-pulse/ : 'none')
      // Motion preferences never hide progress, errors, or interaction targets.
      await expect(indicator).toBeVisible()
      await expect(composer).toHaveClass(/composer--running/)
      await expect(summary).toContainText('1 failed')
      await expect(page.getByRole('button', { name: 'Stop response', exact: true })).toBeVisible()
    }
    await expectAnimations([true, true, true])

    const settings = await context.newPage()
    await settings.setViewportSize({ width, height: 900 })
    await settings.goto('/settings/workspace')
    const labels = ['Chat list animation', 'Message window animation', 'Tool-call indicator animation']
    const keys = ['chatListAnimation', 'messageWindowAnimation', 'toolIndicatorAnimation']
    const setAnimations = async (values) => {
      for (let i = 0; i < labels.length; i++) {
        await settings.getByRole('switch', { name: labels[i], exact: true }).setChecked(values[i])
      }
    }
    // Every combination, synchronized live into the already-running chat tab.
    for (let mask = 0; mask < 8; mask++) {
      const values = keys.map((_, i) => Boolean(mask & (1 << i)))
      await setAnimations(values)
      await expectAnimations(values)
    }
    await settings.getByLabel('Motion', { exact: true }).selectOption('reduced')
    await expectAnimations([false, false, false])
    for (const name of labels) await expect(settings.getByRole('switch', { name, exact: true })).toBeChecked()
    await settings.getByLabel('Motion', { exact: true }).selectOption('system')
    await expectAnimations([true, true, true])
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await expectAnimations([false, false, false])
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await expectAnimations([true, true, true])

    const saved = [false, true, false]
    await setAnimations(saved)
    await expectAnimations(saved)
    await settings.reload()
    for (let i = 0; i < labels.length; i++) {
      await expect(settings.getByRole('switch', { name: labels[i], exact: true })).toBeChecked({ checked: saved[i] })
    }
    await page.reload()
    await expectAnimations(saved)
    const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('nautionette.preferences.v1')))
    expect(keys.map(key => stored[key])).toEqual(saved)
    await summary.click()
    await expect(page.locator('.tool-group__timeline')).toBeVisible()
    await summary.click()
    await settings.getByRole('button', { name: 'Reset workspace', exact: true }).click()
    await expectAnimations([true, true, true])
    await settings.close()
    await page.screenshot({ path: `test-results/tool-indicator-running-${width}.png` })
  })
}
