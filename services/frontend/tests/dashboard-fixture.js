import { mockAgents } from './agent-fixture.js'
import { createSSEFixture } from './sse-fixture.js'

export async function mockDashboards (context) {
  const base = await mockAgents(context)
  const agent = base.addAgent('Interface builder')
  const sse = await createSSEFixture(context)
  const now = Date.now() / 1000
  const action = { label: 'Validate candidate', workflow: 'dashboard_check', inputs: {}, bindings: { commit: { source: 'feed', path: '/commit' } }, conditions: [], input_schema: { type: 'object', properties: { count: { type: 'integer', minimum: 1, default: 1, title: 'Count' } }, required: ['count'], additionalProperties: false }, confirm: true, confirmation: 'This runs a test workflow, not a deployment.', enabled: true, reason: '', context_hash: 'candidate-one', resolved_inputs: { commit: 'abc123' } }
  const widget = (id, title, component, props, span = 4) => ({ id, title, component, source: 'feed', span, props: {}, bindings: {}, parts: [{ component, props }], error: '' })
  const view = { id: 'test-interface', revision: 1, current_revision: 1, manifest: { title: 'Test interface', agent_id: agent.id, description: 'Generic capabilities exercised with fixture data.', pinned: true }, observed_at: now,
    widgets: [widget('total', 'Candidate count', 'metric', { value: 7, label: 'Ready', tone: 'success' }), widget('state', 'Status', 'status', { value: 'Healthy', tone: 'success' }), widget('action', 'Workflow', 'action', { action: 'check' }), widget('table', 'Recent changes', 'table', { columns: [{ key: 'commit', label: 'Commit' }, { key: 'title', label: 'Title' }], items: [{ commit: 'abc123', title: 'A very long change title '.repeat(20) }] }, 8), widget('note', 'Agent note', 'text', { text: '<img src=x onerror=alert(1)> remains text' })],
    sources: { feed: { state: 'fresh', data: { commit: 'abc123' }, observed_at: now, stale_after: now + 180, error: '' } }, actions: { check: action }, runs: [] }
  const state = { base, agent, view, writes: [], actions: [], streamReads: 0, failAction: false, snapshots: 0 }
  const summary = () => ({ id: view.id, revision: view.revision, ...view.manifest, url: `/dashboards/${view.id}` })
  await context.route('**/api/dashboards**', async route => {
    const path = new URL(route.request().url()).pathname
    const method = route.request().method()
    if (path === '/api/dashboards') return route.fulfill({ json: { dashboards: [summary()], next_offset: null } })
    if (path.endsWith('/stream')) return sse(route, () => { state.streamReads++; return `data: ${JSON.stringify({ type: 'snapshot', snapshot: view })}\n\n` })
    if (path.endsWith('/snapshot')) { state.snapshots++; return route.fulfill({ json: view }) }
    if (path.endsWith('/pin') && method === 'PATCH') {
      const payload = route.request().postDataJSON(); state.writes.push(payload)
      view.revision++; view.manifest.pinned = payload.pinned; view.observed_at = Date.now() / 1000
      return route.fulfill({ json: view })
    }
    if (path.endsWith('/actions/check') && method === 'POST') {
      const body = route.request().postDataJSON(); state.actions.push(body)
      if (state.actionGate) await state.actionGate
      if (state.failAction) return route.fulfill({ status: 503, json: { detail: { message: 'Workflow start could not be confirmed. Retry the SAME request ID.', workflow_id: 'check-once' } } })
      view.runs = [{ workflow_id: 'check-once', action_id: 'check', workflow: 'dashboard_check', status: 'running', url: '/runs/check-once', result: null, request: body, request_id: body.request_id }]
      view.observed_at = Date.now() / 1000
      return route.fulfill({ json: view.runs[0] })
    }
    return route.fulfill({ status: 404, json: { detail: 'Not found' } })
  })
  return state
}
