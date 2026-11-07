import { api, auth, server } from './api.js'

export function desktopOAuth () {
  const bridge = globalThis.nautionetteDesktop?.oauth
  return bridge?.version === 1 && ['prepare', 'authorize', 'cancel'].every(key => typeof bridge[key] === 'function') ? bridge : null
}

export const oauthClientMessage = 'CLI OAuth requires an updated Nautionette Windows, macOS or Linux desktop app. Switch from the web/mobile client, or update an older desktop app.'

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

// No credentials/callbacks in reactive state, localStorage, chat or URL params.
// Each new CLI authorization URL gets its own exact port and one-shot receiver.
export function oauthConnection ({ bridge = desktopOAuth(), backend = api, interval = 1000,
  context = () => `${server.url}|${auth.token}` } = {}) {
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
  async function receive (authorization) {
    const receiver = await bridge.prepare(authorization.redirect_uri)
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
    try {
      check()
      onStage('Starting CLI login inside agentgateway…')
      const login = await backend.startMcpOAuth(name, { ...config, desktop_bridge_version: bridge.version })
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
          const callback = await receive(authorization)
          check()
          onStage('Forwarding callback to the CLI; checking authentication and MCP…')
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
