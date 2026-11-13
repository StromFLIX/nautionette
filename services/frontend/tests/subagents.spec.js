import { test, expect } from '@playwright/test'
import { mockAgents } from './agent-fixture.js'

for (const width of [1440, 320]) {
  test(`Agents group, allowlist and global limits at ${width}px`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 1000 })
    const state = await mockAgents(context)
    const researcher = state.addAgent('Researcher')
    const lead = state.addAgent('Lead')
    const names = ['list_subagents', 'call_subagent', 'wait_for_subagent']
    state.catalog.tools.push(...names.map(name => ({ name, server: 'Agents', description: 'Delegate work' })))
    await page.goto('/settings/general#subagent-limits')
    await page.getByLabel('Maximum depth', { exact: true }).fill('2')
    await page.getByLabel('Maximum children per turn', { exact: true }).fill('7')
    await page.getByLabel('Maximum parallel calls per tree', { exact: true }).fill('3')
    await page.locator('.settings__save').getByRole('button', { name: 'Save', exact: true }).click()
    await expect.poll(() => state.settings.subagent_max_depth).toBe(2)
    expect(state.settings.subagent_max_width).toBe(7)
    expect(state.settings.subagent_max_parallel).toBe(3)
    await page.getByLabel('Default tools', { exact: true }).click()
    const group = page.getByRole('checkbox', { name: 'Agents tool group', exact: true })
    await expect(group).toHaveAttribute('aria-checked', 'true')
    await group.click()
    await expect(group).toHaveAttribute('aria-checked', 'false')
    await page.keyboard.press('Escape')
    await page.locator('.settings__save').getByRole('button', { name: 'Save', exact: true }).click()
    await expect.poll(() => state.settings.default_tools).not.toBeNull()
    expect(names.some(name => state.settings.default_tools.includes(name))).toBe(false)
    await page.getByLabel('Default tools', { exact: true }).click()
    await page.getByRole('checkbox', { name: 'Agents tool group', exact: true }).click()
    await page.keyboard.press('Escape')
    await page.locator('.settings__save').getByRole('button', { name: 'Save', exact: true }).click()
    await expect.poll(() => names.every(name => state.settings.default_tools.includes(name))).toBe(true)
    await page.goto(`/settings/agents?edit=${lead.id}#agent-profiles`)
    await expect(page.getByRole('form', { name: 'Agent configuration' })).toBeVisible()
    const allowed = page.getByRole('group', { name: 'Allowed subagents', exact: true })
    await expect(allowed.getByRole('checkbox', { name: 'Researcher', exact: true })).not.toBeChecked()
    await allowed.getByRole('checkbox', { name: 'Researcher', exact: true }).check()
    await page.getByRole('button', { name: 'Save agent', exact: true }).click()
    await expect.poll(() => lead.allowed_subagents).toEqual([researcher.id])
    await expect(page.getByRole('form', { name: 'Agent configuration' })).not.toBeVisible()
    await expect(page).not.toHaveURL(/edit=/)
    await page.reload()
    await page.getByRole('button', { name: 'Edit Lead', exact: true }).click()
    await expect(page.getByRole('group', { name: 'Allowed subagents' }).getByRole('checkbox', { name: 'Researcher', exact: true })).toBeChecked()
    expect(await page.locator('.settings__body').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
  })
}

test('messages show sending agent identities, not a human sender', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.data.messages = [
    { id: 'delegated', role: 'user', content: 'Please research this', meta: { sender_agent: { id: 'lead', name: 'Lead', chat_id: 'source' } }, created_at: 1700000000 },
    { id: 'answer', role: 'assistant', content: 'Research complete', meta: { sender_agent: { id: 'researcher', name: 'Researcher' } }, created_at: 1700000001 }
  ]
  await page.goto('/chats/alpha')
  await expect(page.locator('#message-delegated .bubble__sender')).toContainText('Lead')
  await expect(page.locator('#message-answer .bubble__sender')).toContainText('Researcher')
  await expect(page.getByRole('link', { name: 'Source chat', exact: true })).toHaveAttribute('href', '/chats/source')
})

for (const width of [1440, 320]) {
  test(`workflow calls, results and nested agents share attribution at ${width}px`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 1100 })
    const state = await mockAgents(context)
    const origin = { kind: 'workflow', relation: 'call', workflow: 'weekly_repository_updates', workflow_id: 'workflow-run', trigger: 'agent' }
    state.data.messages = [
      { id: 'task', role: 'user', content: 'Read-only verification task', meta: { origin }, created_at: 1700000000 },
      { id: 'reply', role: 'assistant', content: 'Verification passed', meta: { origin, sender_agent: { id: 'marvin', name: 'Marvin' } }, created_at: 1700000001 },
      { id: 'nested', role: 'user', content: 'Inspect one dependency', meta: { origin: { kind: 'agent', relation: 'call', agent_id: 'marvin', agent_name: 'Marvin', chat_id: 'parent', root: { ...origin, trigger: 'schedule' } } }, created_at: 1700000002 },
      { id: 'result', role: 'assistant', content: 'Maintenance completed', meta: { run: { workflow: 'weekly_repository_updates', workflow_id: 'workflow-run', status: 'completed' } }, created_at: 1700000003 }
    ]
    await page.goto('/chats/alpha')
    for (const id of ['task', 'reply']) {
      const badge = page.locator(`#message-${id} .message-origin`)
      await expect(badge).toContainText('Called by workflow')
      await expect(badge).toContainText('Agent-started')
      await expect(badge.getByRole('link', { name: 'weekly_repository_updates', exact: true })).toHaveAttribute('href', '/workflows/weekly_repository_updates')
      await expect(badge.getByRole('link', { name: 'View run', exact: true })).toHaveAttribute('href', '/runs/workflow-run')
    }
    await expect(page.locator('#message-reply .bubble__sender')).toContainText('Marvin')
    await expect(page.locator('#message-nested .message-origin')).toContainText('Called by Marvin')
    await expect(page.locator('#message-nested .message-origin')).toContainText('Scheduled')
    await expect(page.locator('#message-nested').getByRole('link', { name: 'Source chat', exact: true })).toHaveAttribute('href', '/chats/parent')
    await expect(page.locator('#message-nested').getByRole('link', { name: 'View workflow run', exact: true })).toHaveAttribute('href', '/runs/workflow-run')
    await expect(page.locator('#message-result .message-origin')).toContainText('Result from')
    await expect(page.locator('#message-result .message-origin')).toContainText('completed')
    expect(await page.locator('.thread__body').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true)
    if (process.env.PROVENANCE_SCREENSHOTS) await page.screenshot({ path: `/workspace/provenance-${width}.png`, fullPage: true })
    await page.reload()
    await expect(page.locator('#message-task .message-origin')).toContainText('Agent-started')
  })
}

test('streaming workflow replies keep their origin after reconnect', async ({ page, context }) => {
  const state = await mockAgents(context)
  state.data.active_turn = {
    id: 'live-turn', steps: [{ kind: 'text', text: 'Checking dependencies' }], status: 'Working',
    sender_agent: { id: 'marvin', name: 'Marvin' },
    origin: { kind: 'workflow', relation: 'call', workflow: 'maintenance', workflow_id: 'scheduled-run', trigger: 'schedule' }
  }
  await page.goto('/chats/alpha')
  const badge = page.locator('.msg--assistant .message-origin')
  await expect(badge).toContainText('Scheduled')
  await expect(badge.getByRole('link', { name: 'View run', exact: true })).toHaveAttribute('href', '/runs/scheduled-run')
  await page.reload()
  await expect(badge).toContainText('Scheduled')
})
