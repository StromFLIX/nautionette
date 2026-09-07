import { test, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../../', import.meta.url))
const source = readFileSync(`${root}/workflows/url_digest.py`, 'utf8')
const proposedSource = source.replace('"save_artifact"', '"notify_warehouse"').replace('timedelta(minutes=10)', 'timedelta(minutes=12)')
function python (script, input) {
  return JSON.parse(execFileSync('uv', ['run', 'python', '-c', script], { cwd: root, input: JSON.stringify(input), encoding: 'utf8' }))
}
function definition (code) {
  return python('import sys,json; from nautionette_backend.workflow_graph import definition_graph; print(json.dumps(definition_graph(json.load(sys.stdin), "url_digest")))', code)
}
const original = definition(source)
const proposed = definition(proposedSource)
const grouped = definition(`
from temporalio import workflow
@workflow.defn(name="url_digest")
class Digest:
  @workflow.run
  async def run(self, params):
    for repository in params["repositories"]:
      issues = await workflow.execute_activity("mcp_call", {
        "tool": "github_search_issues", "arguments": {"repo": repository}
      })
      for issue in issues:
        await workflow.execute_activity("agent_call", {
          "agent_set": "research", "prompt": f"Summarise {issue}",
          "output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}}
        })
    await workflow.execute_activity("save_artifact", {"name": "digest.md", "content": params["summary"]})
    return {"ok": True}
`)
const start = Date.now() - 45000
const at = (seconds) => new Date(start + seconds * 1000).toISOString()
const info = { workflow_id: 'digest-run', run_id: 'temporal-run', workflow_type: 'url_digest', status: 'RUNNING', start_time: at(0), close_time: null }
const history = [
  { id: 1, event: 'workflow.started', at: at(0), input: { url: 'https://pi.dev' }, task_queue: 'nautionette' },
  { id: 5, event: 'activity.scheduled', at: at(1), activity: 'http_fetch', activity_id: 'fetch', workflow_task_completed_event_id: 4, task_queue: 'nautionette' },
  { id: 6, event: 'activity.started', at: at(2), scheduled_event_id: 5 },
  { id: 7, event: 'activity.completed', at: at(3), scheduled_event_id: 5, result: { status: 200 } },
  { id: 10, event: 'activity.scheduled', at: at(4), activity: 'agent_call', activity_id: 'agent', workflow_task_completed_event_id: 9, task_queue: 'nautionette', input: { prompt: 'Summarise the page' } },
  { id: 11, event: 'activity.started', at: at(5), scheduled_event_id: 10, attempt: 1 }
]
function execution (completed = false) {
  return python('import sys,json; from nautionette_backend.execution_graph import execution_graph; data=json.load(sys.stdin); print(json.dumps(execution_graph(data["info"],data["history"])))', {
    info: { ...info, status: completed ? 'COMPLETED' : 'RUNNING', close_time: completed ? at(30) : null },
    history: completed ? [...history,
      { id: 14, event: 'activity.completed', at: at(25), scheduled_event_id: 10, result: { summary: 'A coding agent toolkit.', tools: ['github_search_issues'] } },
      { id: 17, event: 'activity.scheduled', at: at(26), activity: 'save_artifact', workflow_task_completed_event_id: 16 },
      { id: 18, event: 'activity.started', at: at(27), scheduled_event_id: 17 },
      { id: 19, event: 'activity.completed', at: at(28), scheduled_event_id: 17 },
      { id: 22, event: 'workflow.completed', at: at(30), result: { summary: 'A coding agent toolkit.' } }
    ] : history
  })
}
const liveGraph = execution()
const finishedGraph = execution(true)

async function mockApi (page, options = {}) {
  const state = { graph: liveGraph, requests: 0, ...options }
  const run = { workflow_id: 'digest-run', workflow: 'url_digest', status: 'running', input: { url: 'https://pi.dev' }, created_at: start / 1000, trigger: 'manual' }
  const workflow = { name: 'url_digest', title: 'URL digest', description: 'Fetch a page, summarize it, and save the result.', code: source, graph: state.definition || original, manifest: { inputs: {} }, runs: [run], settings: {} }
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let data = {}
    if (path === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' })
    if (path === '/api/system') data = { components: [], agent_sets: [] }
    if (path === '/api/catalog') data = { models: [], tools: [], agent_sets: [] }
    if (path === '/api/chats') data = { chats: [] }
    if (path === '/api/workflows') data = { workflows: [workflow] }
    if (path === '/api/workflows/url_digest') data = workflow
    if (path === '/api/drafts') data = { drafts: state.draft ? [{ name: 'url_digest', title: 'URL digest' }] : [] }
    if (path === '/api/drafts/url_digest') data = { name: 'url_digest', code: proposedSource, graph: proposed, previous_graph: state.isNew ? null : original, diff: '-save_artifact\n+notify_warehouse', validation: { valid: true, errors: [], steps: [] }, meta: { message: 'Notify the warehouse after summarizing' } }
    if (path === '/api/runs') data = { runs: [run] }
    if (path === '/api/runs/digest-run') data = { run, temporal: info }
    if (path === '/api/runs/digest-run/graph') {
      state.requests++
      if (state.offline) return route.fulfill({ status: 503, json: { detail: 'Temporal unavailable' } })
      data = state.graph
    }
    return route.fulfill({ json: data })
  })
  return state
}

test('definition canvas supports inspection, search, direction, zoom, and full screen', async ({ page }) => {
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await mockApi(page)
  await page.goto('/workflows/url_digest')
  await expect(page.locator('.flow-node')).toHaveCount(original.nodes.length)
  await page.getByRole('button', { name: 'Fit all steps', exact: true }).click()
  await page.locator('.flow-node').filter({ hasText: 'http_fetch' }).click()
  await expect(page.getByRole('complementary', { name: 'Step details' })).toContainText('http_fetch')
  await expect(page.getByRole('complementary', { name: 'Step details' })).toContainText('execute_activity')
  await page.getByRole('button', { name: 'Close step details' }).click()
  await page.getByRole('button', { name: 'Search steps', exact: true }).click()
  await page.getByRole('textbox', { name: 'Search steps' }).fill('agent_call')
  await page.getByRole('button', { name: 'Next matching step' }).click()
  await expect(page.getByRole('complementary', { name: 'Step details' })).toContainText('agent_call')
  await page.getByRole('button', { name: 'Close step details' }).click()
  await page.getByRole('button', { name: 'Search steps', exact: true }).click()
  await expect(page.locator('.flow-node--dimmed')).toHaveCount(0)
  await page.getByRole('button', { name: 'Change flow direction' }).click()
  await page.getByRole('button', { name: 'Full screen', exact: true }).click()
  await expect(page.locator('.flow--expanded')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.flow--expanded')).toHaveCount(0)
  await page.getByRole('button', { name: 'Change flow direction' }).click()
  await page.getByRole('button', { name: 'Fit all steps', exact: true }).click()
  await page.mouse.move(410, 10)
  await expect(page.locator('.flow__scale')).not.toHaveText('100%')
  await page.screenshot({ path: '/tmp/nautionette-flow-desktop.png' })
  expect(errors).toEqual([])
})

test('live completion preserves selection and camera, then stops polling', async ({ page }) => {
  const state = await mockApi(page)
  await page.goto('/runs/digest-run')
  await expect(page.locator('.flow-node--running')).toHaveCount(2)
  await page.getByRole('button', { name: 'Focus active step' }).click()
  await page.locator('.flow-node').filter({ hasText: 'agent_call' }).click()
  await page.getByRole('button', { name: 'Zoom out', exact: true }).click()
  const canvas = page.locator('.vue-flow__transformationpane')
  await expect(canvas).toHaveAttribute('style', /transform/)
  const camera = await canvas.getAttribute('style')
  state.graph = finishedGraph
  await expect(page.locator('.flow-node--running')).toHaveCount(0, { timeout: 8000 })
  await expect(page.getByRole('complementary', { name: 'Step details' })).toContainText('A coding agent toolkit.')
  await expect(page.getByRole('complementary', { name: 'Step details' })).toContainText('Tools used')
  await expect(page.getByRole('complementary', { name: 'Step details' })).toContainText('github_search_issues')
  expect(await canvas.getAttribute('style')).toBe(camera)
  const requests = state.requests
  await page.clock.install()
  await page.clock.fastForward(6000)
  expect(state.requests).toBe(requests)
})

test('draft review displays additions, changes, removals and both versions', async ({ page }) => {
  await mockApi(page, { draft: true })
  await page.goto('/workflows/url_digest')
  await expect(page.locator('.flow-node--added')).toHaveCount(1)
  await expect(page.locator('.flow-node--removed')).toHaveCount(1)
  await expect(page.locator('.flow-node--changed')).toHaveCount(2)
  await page.getByRole('button', { name: 'Fit all steps', exact: true }).click()
  await expect(page.locator('.flow__scale')).not.toHaveText('100%')
  await page.screenshot({ path: '/tmp/nautionette-flow-draft.png' })
  await page.getByRole('combobox', { name: 'Draft comparison version' }).selectOption('deployed')
  await expect(page.locator('.flow-node').filter({ hasText: 'notify_warehouse' })).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Draft comparison version' }).selectOption('proposed')
  await expect(page.locator('.flow-node').filter({ hasText: 'save_artifact' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Code diff', exact: true }).click()
  await expect(page.locator('pre')).toContainText('+notify_warehouse')
})

for (const width of [390, 320]) {
  test(`mobile ${width}px keeps graph controls and inspector within the viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 320 ? 568 : 844 })
    await mockApi(page)
    await page.goto('/runs/digest-run')
    await expect(page.locator('.flow-node')).toHaveCount(3)
    await expect(page.locator('.flow-node').filter({ hasText: 'agent_call' })).toBeInViewport({ ratio: 1 })
    await page.screenshot({ path: `/tmp/nautionette-flow-mobile-${width}.png` })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const toolbar = await page.locator('.flow__zoom').boundingBox()
    expect(toolbar.x).toBeGreaterThanOrEqual(0)
    expect(toolbar.x + toolbar.width).toBeLessThanOrEqual(width)
    await page.getByRole('button', { name: 'Focus active step' }).click()
    await page.locator('.flow-node').filter({ hasText: 'agent_call' }).click()
    const inspector = await page.getByRole('complementary', { name: 'Step details' }).boundingBox()
    expect(inspector.x).toBeGreaterThanOrEqual(0)
    expect(inspector.width).toBeLessThanOrEqual(width)
    await page.getByRole('button', { name: 'Close step details' }).click()
    await page.getByRole('button', { name: 'Details', exact: true }).click()
    await expect(page.getByText('Input', { exact: true })).toBeVisible()
  })
}

test('mobile new-flow review and live connection failure remain usable', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 })
  const state = await mockApi(page, { draft: true, isNew: true })
  await page.goto('/workflows/url_digest')
  await expect(page.locator('.flow-node--added')).toHaveCount(proposed.nodes.length)
  await expect(page.getByRole('button', { name: 'Approve and deploy' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.goto('/runs/digest-run')
  await expect(page.locator('.flow-node')).toHaveCount(3)
  state.offline = true
  await expect(page.getByRole('status')).toContainText('Live updates paused', { timeout: 8000 })
  await expect(page.locator('.flow-node')).toHaveCount(3)
  state.offline = false
  await page.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(page.getByRole('status')).toHaveCount(0)
})

for (const width of [1440, 320]) {
  test(`nested loop groups and rich activity details at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 320 ? 700 : 1100 })
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    await mockApi(page, { definition: grouped })
    await page.goto('/workflows/url_digest')
    await expect(page.locator('.flow-loop')).toHaveCount(2)
    await page.getByRole('button', { name: 'Fit all steps', exact: true }).click()
    for (const direction of ['TB', 'LR']) {
      const boxes = await page.locator('.vue-flow__node').evaluateAll((elements) => Object.fromEntries(elements.map((element) => {
        const box = element.getBoundingClientRect()
        return [element.dataset.id, { x: box.x, y: box.y, right: box.right, bottom: box.bottom }]
      })))
      for (const node of grouped.nodes.filter((item) => item.parent_id)) {
        const child = boxes[node.id]
        const parent = boxes[node.parent_id]
        expect(child.x).toBeGreaterThan(parent.x)
        expect(child.y).toBeGreaterThan(parent.y)
        expect(child.right).toBeLessThan(parent.right)
        expect(child.bottom).toBeLessThan(parent.bottom)
      }
      await expect(page.locator('.flow-loop').first()).toHaveCSS('border-top-style', 'dashed')
      if (direction === 'TB') await page.screenshot({ path: `/tmp/nautionette-grouped-${width}.png` })
      await page.getByRole('button', { name: 'Change flow direction' }).click()
    }
    await page.getByRole('button', { name: 'Search steps', exact: true }).click()
    await page.getByRole('textbox', { name: 'Search steps' }).fill('research')
    await page.getByRole('button', { name: 'Next matching step' }).click()
    const inspector = page.getByRole('complementary', { name: 'Step details' })
    await expect(inspector).toContainText('Agent: research')
    await expect(inspector).toContainText('Output schema')
    await expect(inspector).toContainText('Expression')
    const selected = page.locator('.flow-node--selected')
    await expect(selected).toBeInViewport({ ratio: 1 })
    if (width === 320) {
      await expect.poll(async () => {
        const nodeBox = await selected.boundingBox()
        const detailsBox = await inspector.boundingBox()
        return nodeBox.y + nodeBox.height <= detailsBox.y
      }).toBe(true)
    }
    await page.mouse.move(5, 5)
    await page.screenshot({ path: `/tmp/nautionette-activity-details-${width}.png` })
    await page.getByRole('button', { name: 'Close step details' }).click()
    await page.getByRole('textbox', { name: 'Search steps' }).fill('github_search_issues')
    await page.getByRole('button', { name: 'Next matching step' }).click()
    await expect(inspector).toContainText('Server')
    await expect(inspector).toContainText('github')
    await expect(inspector).toContainText('Arguments')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(errors).toEqual([])
  })
}