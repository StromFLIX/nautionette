import { reactive } from 'vue'

export const AGENT_NAVIGATION_KEY = 'nautionette.agent-navigation.v1'

// Only navigation metadata is cached, never portraits, prompts or configuration.
function entries (agents) {
  if (!Array.isArray(agents)) return []
  return agents.filter(agent => agent && typeof agent.id === 'string' && agent.id && typeof agent.name === 'string' && agent.name)
    .map(({ id, name }) => ({ id, name }))
}

/** Navigation must not wait for remote model/tool discovery or image decoding. */
export function createAgentNavigation ({ scope, fetchAgents, storage = () => sessionStorage }) {
  const key = () => `${AGENT_NAVIGATION_KEY}:${encodeURIComponent(scope())}`
  function restore () {
    try { return entries(JSON.parse(storage().getItem(key()) || '[]')) } catch { return [] }
  }
  const state = reactive({ agents: restore(), loaded: false, error: '' })
  let request = 0

  function sync (agents) {
    request++ // A delayed read must not undo an authoritative profile edit.
    state.agents = agents
    state.loaded = true
    state.error = ''
    try { storage().setItem(key(), JSON.stringify(entries(agents))) } catch { /* Optional, tab-local cache. */ }
  }

  return {
    state, sync,
    async load () {
      const target = scope(), current = ++request
      try {
        const data = await fetchAgents()
        if (target !== scope() || current !== request) return
        if (!Array.isArray(data?.agents)) throw new Error('Could not load agents.')
        sync(data.agents)
      } catch (error) {
        if (target === scope() && current === request) state.error = error.message || 'Could not load agents.'
      }
    },
    clear () {
      request++
      state.agents = []
      state.loaded = false
      state.error = ''
      try { storage().removeItem(key()) } catch { /* Storage may be disabled. */ }
    }
  }
}
