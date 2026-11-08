// Provider-neutral relogin prompts. Device snoozes contain no OAuth credentials.
export const MCP_SNOOZE_KEY = 'nautionette.mcp-login-snooze'
export const MCP_SNOOZE_MAX = 7 * 86400000
export function createMcpAuthState({ state, scope, fetchStatus, deviceScope = () => 'local',
  storage = () => null, now = Date.now, schedule = setTimeout, unschedule = clearTimeout,
  events = null, onError = () => {} }) {
  let request = 0, timer = null, disposed = false
  const seen = new Set()
  const incident = item => `${scope()}|${item.name}|${item.revision || ''}|${item.status}`
  function read() {
    try {
      const text = storage()?.getItem(MCP_SNOOZE_KEY) || '{}'
      if (text.length > 50000) return {}
      const value = JSON.parse(text)
      if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
      return Object.fromEntries(Object.entries(value).filter(([key, until]) =>
        key.length <= 2048 && Number.isSafeInteger(until) && until > now() && until <= now() + MCP_SNOOZE_MAX))
    } catch { return {} }
  }
  function updateSnooze() {
    const value = read()[deviceScope()] || 0
    state.snoozedUntil = value
    return value
  }
  function arm() {
    if (timer !== null) unschedule(timer)
    timer = null
    const until = activeSnooze(), context = deviceScope()
    if (until && !disposed) {
      timer = schedule(() => {
        timer = null
        if (!disposed && context === deviceScope()) { activeSnooze(); refresh().catch(onError) }
      }, Math.max(1, until - now()))
      timer?.unref?.()
    }
  }
  function clear() {
    request++
    seen.clear()
    state.connections = []
    state.prompt = null
    state.busyName = ''
    arm() // Device preferences survive logout, reload and token changes.
  }
  function open(item) {
    seen.add(incident(item))
    state.prompt = { ...item } // Explicit Login is available even while snoozed.
  }
  function dismiss() { state.prompt = null }
  function begin(name) {
    state.busyName = name
    const item = state.connections.find(item => item.name === name)
    if (item) seen.add(incident(item))
  }
  function finish(name) { if (state.busyName === name) state.busyName = '' }
  function forgetSeen() {
    for (const item of state.connections) seen.delete(incident(item))
  }
  function snooze(duration) {
    if (state.busyName || !Number.isSafeInteger(duration) || duration < 60000 || duration > MCP_SNOOZE_MAX) return false
    const values = read(), until = now() + duration
    values[deviceScope()] = until
    try { storage()?.setItem(MCP_SNOOZE_KEY, JSON.stringify(values)) } catch { /* Session fallback below. */ }
    // Keep a fallback for browsers refusing local storage, without credentials.
    sessionSnooze = { context: deviceScope(), until }
    forgetSeen()
    state.prompt = null
    arm()
    return true
  }
  let sessionSnooze = null
  function activeSnooze() {
    const ignored = sessionSnooze?.context === deviceScope() && sessionSnooze.ignore
    const stored = ignored ? 0 : updateSnooze()
    const fallback = sessionSnooze?.context === deviceScope() && sessionSnooze.until > now() ? sessionSnooze.until : 0
    state.snoozedUntil = Math.max(stored, fallback)
    return state.snoozedUntil
  }
  async function resume() {
    const values = read()
    delete values[deviceScope()]
    sessionSnooze = null
    try { storage()?.setItem(MCP_SNOOZE_KEY, JSON.stringify(values)) } catch {
      sessionSnooze = { context: deviceScope(), until: 0, ignore: true }
    }
    forgetSeen()
    arm()
    return refresh()
  }
  async function refresh() {
    const context = scope(), id = ++request
    const result = await fetchStatus()
    if (disposed || context !== scope() || id !== request || !result) return
    state.connections = Array.isArray(result.connections) ? result.connections : []
    if (state.prompt && !state.busyName) {
      const current = state.connections.find(item => item.name === state.prompt.name)
      if (!current || current.status === 'connected') state.prompt = null
    }
    if (state.busyName) {
      const active = state.connections.find(item => item.name === state.busyName)
      if (active) seen.add(incident(active))
    }
    const until = activeSnooze()
    if (until) arm()
    if (state.prompt || state.busyName || until) return
    const next = state.connections.find(item => item.status === 'reauth_required' && !seen.has(incident(item)))
    if (next) open(next) // Never launch a browser just because reminders resume.
  }
  function changed(event) {
    if (event?.key && event.key !== MCP_SNOOZE_KEY) return
    const until = read()[deviceScope()] || 0
    if (until === (state.snoozedUntil || 0)) return // Another instance's choice is unrelated.
    sessionSnooze = null
    forgetSeen()
    state.snoozedUntil = until
    if (until && !state.busyName) state.prompt = null
    arm()
    if (!until) refresh().catch(onError)
  }
  function focused() {
    if (state.snoozedUntil && !activeSnooze()) refresh().catch(onError)
  }
  events?.addEventListener?.('storage', changed)
  events?.addEventListener?.('focus', focused)
  arm()
  function dispose() {
    disposed = true
    request++
    if (timer !== null) unschedule(timer)
    events?.removeEventListener?.('storage', changed)
    events?.removeEventListener?.('focus', focused)
  }
  return { clear, open, dismiss, begin, finish, refresh, snooze, resume, dispose }
}
