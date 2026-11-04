import assert from 'node:assert/strict'
import { test } from 'node:test'
import { registerSubagentTools, SUBAGENT_TOOLS } from '../../images/agent-sets/default/extensions/nautionette/subagents.ts'

const grant = { grant: 'a-private-capability-at-least-32-characters' }
const wire = data => ({ content: [{ type: 'text', text: JSON.stringify(data) }] })
function tools (rpc, allowed = null, capability = grant) {
  const result = new Map()
  registerSubagentTools({ registerTool: tool => result.set(tool.name, tool) }, rpc, capability, allowed)
  return result
}

test('Agents tools are selectable independently, with no capability in schemas', () => {
  const all = tools(() => {})
  assert.deepEqual([...all.keys()], ['list_subagents', 'call_subagent', 'wait_for_subagent'])
  assert.equal(tools(() => {}, new Set()).size, 0)
  assert.deepEqual([...tools(() => {}, new Set(['call_subagent'])).keys()], ['call_subagent'])
  assert.equal(tools(() => {}, null, null).size, 0)
  assert.ok(!JSON.stringify([...all.values()]).includes(grant.grant))
  assert.ok(SUBAGENT_TOOLS.has('backend_call_subagent'))
})

test('call injects private capability and stable request ID but never returns it', async () => {
  const requests = []
  const all = tools(async (method, params) => { requests.push({ method, params }); return wire({ id: 'call', state: 'running' }) })
  const result = await all.get('call_subagent').execute('tool-call-1', { agent_id: 'research', text: 'Task' })
  assert.equal(requests[0].params.name, 'backend_call_subagent')
  assert.deepEqual(requests[0].params.arguments.body, { ...grant, request_id: 'tool-call-1', agent_id: 'research', text: 'Task' })
  assert.ok(!JSON.stringify(result).includes(grant.grant))
  assert.equal(result.details.state, 'running')
})

test('wait long polls in bounded chunks and can poll without waiting', async () => {
  const requests = []
  const all = tools(async (_method, params) => {
    requests.push(params.arguments.body)
    return wire({ state: requests.length === 1 ? 'running' : 'completed', text: 'Answer', ok: true })
  })
  const result = await all.get('wait_for_subagent').execute('wait', { call_id: 'child', timeout_seconds: 30 })
  assert.equal(requests.length, 2)
  assert.ok(requests.every(body => body.timeout_seconds <= 20 && body.grant === grant.grant))
  assert.equal(result.details.text, 'Answer')
  requests.length = 0
  await all.get('wait_for_subagent').execute('poll', { call_id: 'child', timeout_seconds: 0 })
  assert.equal(requests.length, 1)
  assert.equal(requests[0].timeout_seconds, 0)
})

test('errors and cancellation are not reported as successful delegation', async () => {
  const failed = tools(async () => ({ isError: true, content: [{ type: 'text', text: 'Not allowed' }] }))
  await assert.rejects(failed.get('call_subagent').execute('call', { agent_id: 'no', text: 'Task' }), /Not allowed/)
  const controller = new AbortController()
  controller.abort()
  const cancelled = tools(async () => assert.fail('transport must not run'))
  await assert.rejects(cancelled.get('list_subagents').execute('list', {}, controller.signal), /abort/i)
})
