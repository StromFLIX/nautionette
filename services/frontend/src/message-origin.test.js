import { test } from 'node:test'
import assert from 'node:assert/strict'
import { messageOrigin, originLinks, triggerLabel } from './message-origin.js'

test('new calls and legacy results normalize to the shared model', () => {
  const origin = { kind: 'workflow', relation: 'call', workflow: 'maintenance', workflow_id: 'wf-run', trigger: 'schedule' }
  assert.equal(messageOrigin({ origin }), origin)
  assert.deepEqual(messageOrigin({ run: { workflow: 'maintenance', workflow_id: 'wf-run', status: 'completed' } }), { kind: 'workflow', relation: 'result', workflow: 'maintenance', workflow_id: 'wf-run', status: 'completed' })
  assert.deepEqual(originLinks(origin), { workflow: '/workflows/maintenance', run: '/runs/wf-run' })
})

test('assistant authors must never be confused with callers', () => {
  const meta = { sender_agent: { id: 'marvin', name: 'Marvin', chat_id: 'source' } }
  assert.equal(messageOrigin(meta, 'assistant'), null)
  assert.equal(messageOrigin(meta, 'user').agent_name, 'Marvin')
  assert.equal(originLinks(messageOrigin(meta, 'user')).chat, '/chats/source')
})

test('triggers are explicit, not guessed from a workflow ID or title', () => {
  assert.equal(triggerLabel('schedule'), 'Scheduled')
  assert.equal(triggerLabel('agent'), 'Agent-started')
  assert.equal(triggerLabel('manual'), 'Manually started')
  assert.equal(triggerLabel(undefined), '')
  assert.equal(messageOrigin({}), null)
  assert.equal(messageOrigin({ origin: { kind: 'human' } }), null)
  assert.deepEqual(originLinks({ kind: 'workflow', workflow: 'old', workflow_id: null }), { workflow: '/workflows/old', run: null })
})
