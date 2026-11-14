import { brand } from './brand.js'

export function desktopOAuth () {
  const bridge = (globalThis.denkzeugDesktop || globalThis.nautionetteDesktop)?.oauth
  return bridge?.version === 1 && ['prepare', 'authorize', 'cancel'].every(key => typeof bridge[key] === 'function') ? bridge : null
}

export const oauthClientMessage = `OAuth requires an updated ${brand.name} Windows, macOS or Linux desktop app. Switch from the web/mobile client, or update an older desktop app.`

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

// No credentials/callbacks in reactive state, localStorage, chat or URL params.
// Each new CLI authorization URL gets its own exact port and one-shot receiver.
export function oauthConnection ({ bridge = desktopOAuth(), backend, interval = 1000, context } = {}) {
  if (!backend || typeof context !== 'function') throw new Error('OAuth needs a backend adapter and instance context')
  const initialContext = context()
  let cancelled = false, loginId = ''
  function check () {
    if (cancelled) throw new Error('OAuth cancelled')
    if (context() !== initialContext) throw new Error('Instance or credentials changed; start a new login')
  }
  async function cancel () {
    cancelled = true
    await bridge?.cancel().catch(() => {})
    // Never send a callback (or even a cancellation) to a newly selected instance.
    if (loginId && context() === initialContext) await backend.cancelMcpOAuth(loginId).catch(() => {})
  }
  async function receive (authorization, prepared = null) {
    const receiver = prepared || await bridge.prepare(authorization.redirect_uri)
    if (prepared && prepared.redirect_uri !== authorization.redirect_uri) throw new Error('OAuth callback changed after preparation; start again')
    check()
    let finished = false
    const callback = bridge.authorize({ ...authorization, id: receiver.id })
    const watch = (async () => {
      while (!finished) {
        await delay(interval)
        if (finished) return
        check()
        const result = await backend.pollMcpOAuth(loginId)
        if (finished) return
        if (['failed', 'expired', 'cancelled'].includes(result.status)) throw new Error(result.message || 'CLI login stopped')
      }
    })()
    try { return await Promise.race([callback, watch]) } finally { finished = true }
  }
  async function connect (name, config, onStage = () => {}) {
    if (!bridge) throw new Error(oauthClientMessage)
    const seen = new Set()
    const hosted = config.transport === 'http'
    let prepared = null
    try {
      check()
      let payload = { ...config, desktop_bridge_version: bridge.version }
      if (hosted) {
        onStage('Opening the local OAuth callback port…')
        prepared = await bridge.prepare(config.redirect_uri)
        check()
        payload = { ...payload, redirect_uri: prepared.redirect_uri, redirect_uri_template: config.redirect_uri }
      }
      onStage(hosted ? 'Starting hosted HTTPS OAuth…' : 'Starting CLI login inside agentgateway…')
      const login = await backend.startMcpOAuth(name, payload)
      loginId = login.id
      check()
      while (true) {
        check()
        const result = await backend.pollMcpOAuth(loginId)
        check()
        if (result.status === 'complete') { loginId = ''; return result }
        if (['failed', 'expired', 'cancelled'].includes(result.status)) throw new Error(result.message || 'CLI login failed')
        onStage(result.message || 'Waiting for CLI…')
        const authorization = result.authorization
        if (authorization && !seen.has(authorization.id)) {
          seen.add(authorization.id)
          if (seen.size > 8) throw new Error('Too many CLI login attempts; start again')
          onStage(`Sign in in your browser · detected local port ${authorization.port} · ${authorization.response_mode}`)
          const callback = await receive(authorization, prepared)
          prepared = null
          check()
          onStage(hosted ? 'Exchanging the code in the backend; checking HTTPS MCP…' : 'Forwarding callback to the CLI; checking authentication and MCP…')
          // No automatic mutation retry. The gateway fences the capture before
          // contacting its CLI. A lost response is not permission to replay code.
          const forwarded = await backend.forwardMcpOAuth(loginId, { capture_id: authorization.id, ...callback })
          if (forwarded.status !== 'forwarded') throw new Error('Callback delivery is uncertain; start a fresh login. It will not be replayed.')
          if (callback.fields.error) throw new Error('Provider refused or cancelled sign-in')
        }
        await delay(interval)
      }
    } catch (error) {
      await cancel()
      throw error
    } finally { await bridge.cancel().catch(() => {}) }
  }
  return { connect, cancel }
}
