import test from 'node:test'
import assert from 'node:assert/strict'
import { activityLabel, agentActivity } from './agent-activity.js'

test('agent summaries preserve exact counts and distinguish unknown activity from idle', () => {
  assert.equal(agentActivity(null, 'ada'), null)
  assert.equal(activityLabel(null), 'Activity unavailable')
  const activity = { running: 6, queued: 123, agents: { ada: { running: 2, queued: 1 } } }
  assert.equal(agentActivity(activity, null), activity)
  assert.deepEqual(agentActivity(activity, 'ada'), { running: 2, queued: 1 })
  assert.deepEqual(agentActivity(activity, 'idle'), { running: 0, queued: 0 })
  assert.equal(activityLabel(activity), '6 agents running · 123 messages waiting')
  assert.equal(activityLabel({ running: 1, queued: 1 }), '1 agent running · 1 message waiting')
  assert.equal(activityLabel(agentActivity(activity, 'idle')), '0 agents running · 0 messages waiting')
})
