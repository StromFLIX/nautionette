/** Shared defaults / agent / chat semantics. Null and empty lists are deliberate choices. */
export const CONFIG_FIELDS = [
  { key: 'model', id: 'model', label: 'Model' },
  { key: 'agent_set', id: 'agent', label: 'Environment' },
  { key: 'reasoning_effort', id: 'reasoning', label: 'Reasoning' },
  { key: 'tools', id: 'tools', label: 'Tools' },
  { key: 'project_ids', id: 'projects', label: 'Projects' }
]
export const CONFIG_KEYS = CONFIG_FIELDS.map(field => field.key)
const copy = value => Array.isArray(value) ? [...value] : value
export const copyConfig = config => Object.fromEntries(Object.entries(config).map(([key, value]) => [key, copy(value)]))

export function globalChatConfig (catalog) {
  return copyConfig(catalog.global_chat_defaults || {
    model: catalog.default_model || '', agent_set: catalog.default_agent_set || 'default',
    reasoning_effort: catalog.default_reasoning_effort ?? null,
    tools: catalog.default_tools ?? null, project_ids: catalog.default_project_ids || []
  })
}

export function resolveAgentConfig (overrides, defaults) {
  const config = { ...defaults, ...overrides }
  if (config.model !== defaults.model && !Object.hasOwn(overrides, 'reasoning_effort')) config.reasoning_effort = null
  return copyConfig(config)
}

export function agentConfig (catalog, id) {
  const agent = id == null ? null : (catalog.agents || []).find(item => item.id === id)
  if (id != null && !agent) return null
  return {
    agent_id: agent?.id ?? null, agent_name: agent?.name ?? null,
    ...resolveAgentConfig(agent?.config || {}, globalChatConfig(catalog))
  }
}

export function defaultChatConfig (catalog) {
  return copyConfig(catalog.chat_defaults || agentConfig(catalog, catalog.default_agent_id ?? null) || agentConfig(catalog, null))
}

export function sameConfig (left, right) {
  if (!left || !right) return false
  return CONFIG_KEYS.every(key => {
    const a = left[key], b = right[key]
    if (Array.isArray(a) && Array.isArray(b)) return JSON.stringify([...a].sort()) === JSON.stringify([...b].sort())
    return a === b
  })
}

export function toolLabel (tools, total) {
  if (tools === null) return 'All tools'
  return tools.length ? `${tools.length}${total == null ? '' : `/${total}`} tools` : 'No tools'
}
export function projectLabel (ids) {
  return ids.length ? `${ids.length} project${ids.length === 1 ? '' : 's'}` : 'No projects'
}
