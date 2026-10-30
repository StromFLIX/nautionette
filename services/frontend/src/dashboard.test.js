import test from 'node:test'
import assert from 'node:assert/strict'
import { dashboardInputs, dashboardLocation, dashboardOrder, dashboardText, safeDashboardHref } from './dashboard.js'

test('dashboard links reject executable URLs, credentials and protocol-relative URLs', () => {
  for (const value of ['javascript:alert(1)', 'data:text/html,test', '//evil.test', '/\\evil.test', 'https://user:pass@example.com', 'java\nscript:alert(1)', null]) assert.equal(safeDashboardHref(value), '')
  assert.equal(safeDashboardHref('/runs/example'), '/runs/example')
  assert.equal(safeDashboardHref('https://github.com/owner/repo'), 'https://github.com/owner/repo')
})
test('declarative form values are typed without evaluating code', () => {
  const schema = { properties: { count: { type: 'integer' }, enabled: { type: 'boolean' }, settings: { type: 'object' }, optional: { type: 'string' } }, required: ['count'] }
  assert.deepEqual(dashboardInputs(schema, { count: '2', enabled: false, settings: '{"mode":"test"}', optional: '' }), { count: 2, enabled: false, settings: { mode: 'test' } })
  assert.throws(() => dashboardInputs(schema, { count: '2.5' }), /integer/)
  assert.throws(() => dashboardInputs(schema, { count: '' }), /required/)
  assert.throws(() => dashboardInputs(schema, { count: 'NaN' }), /integer/)
  assert.throws(() => dashboardInputs(schema, { count: 2, settings: 'run()' }), /valid JSON/)
})
test('user navigation order is stable and new agent-created dashboards do not displace it', () => {
  const items = [{ id: 'new' }, { id: 'a' }, { id: 'b' }]
  assert.deepEqual(dashboardOrder(items, ['b', 'removed', 'a']).map(item => item.id), ['b', 'a', 'new'])
  assert.equal(items[0].id, 'new')
  assert.deepEqual(dashboardLocation({ id: 'a', agent_id: 'agent-1' }), { path: '/dashboards/a', query: { agent: 'agent-1' } })
})
test('display values stay bounded plain text', () => {
  assert.equal(dashboardText('<script>alert(1)</script>'), '<script>alert(1)</script>')
  assert.equal(dashboardText(null), '—')
  assert.equal(dashboardText('x'.repeat(8000)).length, 4000)
})
