/** Supervise native SSE parsing; the browser's own retry loop is never left running. */
export const LIVE_STALE_MS = 60_000 // Backend sends a heartbeat at least every 20 seconds.
const STABLE_MS = 30_000

export class LiveConnectionError extends Error {
  constructor (message, status = 0, permanent = false) {
    super(message)
    this.status = status
    this.permanent = permanent
  }
}

export function liveConnection (url, { probe, onMessage, onError = () => {}, onOpen = () => {} }, environment = {}) {
  const {
    EventSource = globalThis.EventSource, window = globalThis.window, document = globalThis.document,
    navigator = globalThis.navigator, now = Date.now, random = Math.random,
    schedule = setTimeout, cancel = clearTimeout
  } = environment
  let source = null, checking = null, retryTimer = null, watchdog = null
  let stopped = false, attempts = 0, lastActivity = now(), openedAt = null
  const online = () => navigator?.onLine !== false

  function clearTimers () {
    cancel(retryTimer)
    cancel(watchdog)
    retryTimer = watchdog = null
  }

  function detach () {
    source?.close()
    source = null
    checking?.abort()
    checking = null
    clearTimers()
  }

  function close () {
    stopped = true
    detach()
    window?.removeEventListener('online', resume)
    window?.removeEventListener('offline', offline)
    window?.removeEventListener('focus', resume)
    window?.removeEventListener('pageshow', resume)
    document?.removeEventListener('visibilitychange', resume)
  }

  function retry (error) {
    detach()
    if (stopped) return
    if (error.permanent) close()
    onError(error)
    if (stopped || !online()) return
    // Opening (or receiving a snapshot) is not proof of stability: a server that
    // immediately closes every connection must still back off, not hot-loop.
    const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempts++, 5)) * (0.8 + random() * 0.2)
    retryTimer = schedule(reconnect, delay)
  }

  function watch () {
    cancel(watchdog)
    watchdog = schedule(() => retry(new LiveConnectionError('Live connection stopped responding.')), LIVE_STALE_MS)
  }

  function touch () {
    lastActivity = now()
    if (openedAt !== null && lastActivity - openedAt >= STABLE_MS) attempts = 0
    watch()
  }

  function open () {
    if (stopped || !online()) return
    lastActivity = now()
    openedAt = null
    try {
      const current = source = new EventSource(url)
      watch() // Also bound a request stuck in CONNECTING.
      current.onopen = () => {
        if (source !== current) return
        openedAt = now()
        touch()
        onOpen()
      }
      current.onmessage = event => {
        if (source !== current) return
        touch()
        onMessage(event)
      }
      current.addEventListener('heartbeat', () => { if (source === current) touch() })
      current.onerror = () => {
        if (source === current) retry(new LiveConnectionError('Live connection interrupted.'))
      }
    } catch {
      retry(new LiveConnectionError('Could not open the live connection.', 0, true))
    }
  }

  async function reconnect () {
    retryTimer = null
    if (stopped || !online() || checking) return
    // EventSource hides HTTP status codes. A bounded, authenticated HEAD probe
    // distinguishes a deploy/network outage from rejected credentials/deleted chats.
    const controller = checking = new AbortController()
    const timeout = schedule(() => controller.abort(), 5000)
    try {
      const response = await probe(controller.signal)
      if (checking !== controller || stopped) return
      if (!response.ok) {
        const status = response.status
        // 405 keeps new clients compatible with an older backend during rollout.
        if (status !== 405) {
          const permanent = status >= 400 && status < 500 && ![408, 409, 425, 429].includes(status)
          retry(new LiveConnectionError(`Live connection unavailable (HTTP ${status}).`, status, permanent))
          return
        }
      }
      checking = null
      open()
    } catch {
      if (checking === controller && !stopped) retry(new LiveConnectionError('Could not reach the backend.'))
    } finally { cancel(timeout) }
  }

  function resume () {
    if (stopped || !online() || document?.visibilityState === 'hidden' || checking) return
    if (source && source.readyState !== 2 && now() - lastActivity < LIVE_STALE_MS) return
    detach()
    reconnect()
  }

  function offline () {
    if (stopped) return
    detach()
    onError(new LiveConnectionError('Device is offline.'))
  }

  window?.addEventListener('online', resume)
  window?.addEventListener('offline', offline)
  window?.addEventListener('focus', resume)
  window?.addEventListener('pageshow', resume)
  document?.addEventListener('visibilitychange', resume)
  if (online()) open()
  else offline()
  return { close }
}
