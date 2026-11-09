import { test, expect } from '@playwright/test'
import { mockDesign } from './design-fixture.js'
import { createSSEFixture, snapshotFrame } from './sse-fixture.js'

async function fixture (context, { failure = false } = {}) {
  const state = await mockDesign(context)
  const sse = await createSSEFixture(context)
  state.branches = []
  state.data.chat.message_count = state.data.messages.length
  const branch = {
    chat: { ...state.data.chat, id: 'branched', title: 'Branch: Design review', parent_chat_id: 'alpha', parent_chat_title: 'Design review' },
    messages: structuredClone(state.data.messages).map(message => ({ ...message, id: `branch-${message.id}` })),
    active_turn: null, outputs: []
  }
  await context.route('**/api/chats', route => route.fulfill({ json: { chats: [state.data.chat, ...state.branches] } }))
  await context.route('**/api/chats/alpha/branch', async route => {
    state.branchCalls = (state.branchCalls || 0) + 1
    if (failure) return route.fulfill({ status: 409, json: { detail: 'Wait for the active agent to finish before branching' } })
    state.branches.push(branch.chat)
    return route.fulfill({ json: branch.chat })
  })
  await context.route('**/api/chats/branched', route => route.fulfill({ json: branch }))
  await context.route('**/api/chats/branched/stream', route => sse(route, () => snapshotFrame(branch)))
  await context.route('**/api/chats/branched/read-state', route => route.fulfill({ json: branch.chat }))
  await context.route('**/api/chats/branched/messages', route => {
    const payload = route.request().postDataJSON()
    state.sent.push(payload)
    const message = { id: payload.message_id, chat_id: 'branched', role: 'user', content: payload.text, meta: {}, created_at: Date.now() / 1000 }
    branch.messages.push(message)
    return route.fulfill({ status: 202, json: { message, turn_id: payload.message_id } })
  })
  return state
}

for (const width of [1440, 390]) {
  test(`branch chat opens an independent conversation with a parent link at ${width}px`, async ({ page, context }) => {
    const state = await fixture(context)
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/chats/alpha')
    await expect(page.getByText('A quieter workspace', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Chat options', exact: true }).click()
    await page.getByRole('button', { name: 'Branch chat', exact: true }).click()
    await expect(page).toHaveURL(/\/chats\/branched$/)
    await expect(page.getByText('A quieter workspace', { exact: true })).toBeVisible()
    await expect(page.getByRole('note')).toContainText('Messages and project work are independent')
    await expect(page.getByRole('note')).toContainText('Earlier outputs remain in the original chat')
    await expect(page.getByRole('note').getByRole('link')).toHaveAttribute('href', '/chats/alpha')
    expect(state.branchCalls).toBe(1)
    expect(state.data.messages).toHaveLength(2)
    expect(await page.locator('body').evaluate(element => element.scrollWidth <= innerWidth)).toBe(true)
    await page.getByRole('note').getByRole('link').click()
    await expect(page).toHaveURL(/\/chats\/alpha$/)
    await expect(page.getByRole('note')).toHaveCount(0)
    await expect(page.getByText('A quieter workspace', { exact: true })).toBeVisible()
  })
}

test('branch action works from the chat list without first opening the source', async ({ page, context }) => {
  const state = await fixture(context)
  await page.goto('/chats')
  await page.locator('.chat-list-item').hover()
  await page.getByRole('button', { name: 'Options for Design review', exact: true }).click()
  await page.getByRole('button', { name: 'Branch chat', exact: true }).click()
  await expect(page).toHaveURL(/\/chats\/branched$/)
  expect(state.branchCalls).toBe(1)
  await expect(page.getByRole('note')).toBeVisible()
})

test('branch errors preserve the source conversation and let the user retry', async ({ page, context }) => {
  const state = await fixture(context, { failure: true })
  await page.goto('/chats/alpha')
  await page.getByRole('button', { name: 'Chat options', exact: true }).click()
  await page.getByRole('button', { name: 'Branch chat', exact: true }).click()
  await expect(page.getByText('Wait for the active agent to finish before branching', { exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/chats\/alpha$/)
  await expect(page.getByText('A quieter workspace', { exact: true })).toBeVisible()
  expect(state.branches).toHaveLength(0)
  await page.getByRole('button', { name: 'Chat options', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Branch chat', exact: true })).toBeEnabled()
})

test('branch is disabled while a response is running', async ({ page, context }) => {
  const state = await fixture(context)
  state.data.active_turn = { id: 'running', state: 'running', steps: [], started_at: Date.now() / 1000 }
  state.data.chat.answering = true
  await page.goto('/chats/alpha')
  await page.getByRole('button', { name: 'Chat options', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Branch chat', exact: true })).toBeDisabled()
  expect(state.branchCalls || 0).toBe(0)
})
