import assert from 'node:assert/strict'
import { test } from 'node:test'
import { agentConfig, copyConfig, defaultChatConfig, globalChatConfig, resolveAgentConfig, sameConfig, toolLabel, projectLabel } from './agent-config.js'

const defaults = { model: 'test/reasoner', agent_set: 'default', reasoning_effort: 'high', tools: ['search'], project_ids: ['project'] }

test('old catalogs still produce complete, independent global defaults', () => {
  assert.deepEqual(globalChatConfig({ default_model: 'test/model' }), {
    model: 'test/model', agent_set: 'default', reasoning_effort: null, tools: null, project_ids: []
  })
  const config = globalChatConfig({ global_chat_defaults: defaults })
  config.tools.push('write'); config.project_ids.length = 0
  assert.deepEqual(defaults.tools, ['search'])
  assert.deepEqual(defaults.project_ids, ['project'])
})

test('omission inherits while null and empty lists are explicit agent choices', () => {
  assert.deepEqual(resolveAgentConfig({}, defaults), defaults)
  assert.deepEqual(resolveAgentConfig({ reasoning_effort: null, tools: [], project_ids: [] }, defaults), {
    ...defaults, reasoning_effort: null, tools: [], project_ids: []
  })
  assert.equal(resolveAgentConfig({ tools: null }, defaults).tools, null)
  assert.equal(Object.hasOwn(copyConfig({ tools: null }), 'reasoning_effort'), false)
})

test('inherited reasoning stays with its model and explicit effort wins', () => {
  assert.equal(resolveAgentConfig({ model: 'test/other' }, defaults).reasoning_effort, null)
  assert.equal(resolveAgentConfig({ model: 'test/other', reasoning_effort: 'low' }, defaults).reasoning_effort, 'low')
  assert.equal(resolveAgentConfig({ model: defaults.model }, defaults).reasoning_effort, 'high')
})

test('new chats choose the configured default agent, while explicit global selection is available', () => {
  const catalog = { global_chat_defaults: defaults, default_agent_id: 'writer', agents: [{ id: 'writer', name: 'Writer', config: { tools: [] } }] }
  assert.deepEqual(defaultChatConfig(catalog), { ...defaults, agent_id: 'writer', agent_name: 'Writer', tools: [] })
  assert.deepEqual(agentConfig(catalog, null), { ...defaults, agent_id: null, agent_name: null })
  assert.equal(agentConfig(catalog, 'deleted'), null)
})

test('server-resolved chat defaults are copied without mutating the catalog', () => {
  const catalog = { chat_defaults: { ...defaults, tools: [], agent_id: 'writer' } }
  const config = defaultChatConfig(catalog)
  config.tools.push('dangerous'); config.project_ids.push('other')
  assert.deepEqual(catalog.chat_defaults.tools, [])
  assert.deepEqual(catalog.chat_defaults.project_ids, ['project'])
})

test('custom configuration comparison ignores order, never null versus empty selections', () => {
  assert.equal(sameConfig(defaults, { ...defaults, agent_id: 'id', agent_name: 'name' }), true)
  assert.equal(sameConfig({ ...defaults, tools: ['b', 'a'] }, { ...defaults, tools: ['a', 'b'] }), true)
  assert.equal(sameConfig({ ...defaults, tools: null }, { ...defaults, tools: [] }), false)
  assert.equal(sameConfig(defaults, { ...defaults, project_ids: [] }), false)
  assert.equal(sameConfig(defaults, { ...defaults, reasoning_effort: null }), false)
  assert.equal(sameConfig(defaults, null), false)
})

test('selection labels distinguish all, none, and pinned tools and projects', () => {
  assert.equal(toolLabel(null, 0), 'All tools')
  assert.equal(toolLabel([], 0), 'No tools')
  assert.equal(toolLabel(['read'], 2), '1/2 tools')
  assert.equal(projectLabel([]), 'No projects')
  assert.equal(projectLabel(['a']), '1 project')
})
