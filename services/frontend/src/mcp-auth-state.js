// Provider-neutral relogin prompts. No OAuth credentials or account data here.
export function createMcpAuthState({ state, scope, fetchStatus }) {
  let request = 0
  const seen = new Set()
  const incident = (item) => `${scope()}|${item.name}|${item.revision || ''}|${item.status}`
  function clear() {
    request++
    seen.clear()
    state.connections = []
    state.prompt = null
    state.busyName = ''
  }
  function open(item) {
    seen.add(incident(item))
    state.prompt = { ...item }
  }
  function dismiss() {
    state.prompt = null
  }
  function begin(name) {
    state.busyName = name
    const item = state.connections.find((item) => item.name === name)
    if (item) seen.add(incident(item))
  }
  function finish(name) {
    if (state.busyName === name) state.busyName = ''
  }
  async function refresh() {
    const context = scope(),
      id = ++request
    const result = await fetchStatus()
    if (context !== scope() || id !== request || !result) return
    state.connections = Array.isArray(result.connections) ? result.connections : []
    if (state.prompt && !state.busyName) {
      const current = state.connections.find((item) => item.name === state.prompt.name)
      if (!current || current.status === 'connected') state.prompt = null
    }
    if (state.busyName) {
      const active = state.connections.find(item => item.name === state.busyName)
      if (active) seen.add(incident(active))
    }
    if (state.prompt || state.busyName) return
    const next = state.connections.find(
      (item) => item.status === 'reauth_required' && !seen.has(incident(item)),
    )
    if (next) open(next) // Only show a prompt; never open a browser automatically.
  }
  return { clear, open, dismiss, begin, finish, refresh }
}
