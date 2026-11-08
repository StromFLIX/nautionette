// Temporary loopback receivers only. No token exchange, generic HTTP proxy,
// privileged network access or callback logging is exposed to the renderer.
const http = require('node:http')
const crypto = require('node:crypto')
const LIMIT = 65536

function redirectURL (value, ephemeral = false) {
  const url = new URL(value)
  const port = Number(url.port)
  if (typeof value !== 'string' || value.length > 512 || /[\s\\]/.test(value) ||
      url.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash || !url.port ||
      !((port >= 1024 && port <= 65535) || (ephemeral && port === 0))) {
    throw new Error('Invalid loopback OAuth redirect URI')
  }
  return url
}

function callbackFields (params, state) {
  const fields = {}
  for (const [key, value] of params) {
    if (Object.hasOwn(fields, key) || key.length > 128 || value.length > 16384 ||
        ['access_token', 'refresh_token', 'id_token'].includes(key)) throw new Error('Invalid callback')
    Object.defineProperty(fields, key, { value, enumerable: true })
  }
  if (Object.keys(fields).length > 32 || fields.state !== state ||
      Boolean(fields.code) === Boolean(fields.error)) throw new Error('Invalid callback')
  return fields
}

function callbackPage (returnURL) {
  const nonce = crypto.randomBytes(16).toString('hex')
  const link = typeof returnURL === 'string' && /^nautionette:\/\/oauth-return\/[a-f0-9]{64}$/.test(returnURL)
    ? `<a href="${returnURL}">Open Nautionette</a>` : ''
  return { nonce, html: `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Return to Nautionette</title>
<style>body{font:16px system-ui;background:#101517;color:#e0e8e8;display:grid;place-items:center;min-height:95vh;margin:0}main{max-width:440px;padding:32px}a{display:inline-block;background:#78d9c3;color:#101517;text-decoration:none;padding:12px 20px;border-radius:8px}p{line-height:1.6}</style>
<main><h1>Callback received</h1><p>Nautionette is checking your connection. Returning to your app…</p>${link}<p>If the app stays behind your browser, use the button or switch back to Nautionette. The connection result is shown in the app.</p></main>
<script nonce="${nonce}">history.replaceState(null,'',location.pathname)</script></html>` }
}

function createReceivers ({ openExternal, onReceived = () => {}, returnURL = () => '', timeout = 600000 }) {
  const receivers = new Map()
  function cancel (owner) {
    const receiver = receivers.get(owner)
    if (!receiver) return
    receivers.delete(owner)
    clearTimeout(receiver.timer)
    for (const server of receiver.servers) { server.close(); server.closeAllConnections() }
    receiver.reject?.(new Error('OAuth cancelled or timed out'))
    receiver.resolve = receiver.reject = null
  }

  async function prepare (owner, redirect) {
    if (receivers.has(owner)) throw new Error('An OAuth receiver is already active')
    const url = redirectURL(redirect, true)
    const receiver = { id: crypto.randomBytes(32).toString('hex'), url, state: null, servers: [] }
    receivers.set(owner, receiver)
    async function handle (request, response) {
      const fail = status => { if (!response.headersSent) { response.writeHead(status); response.end('OAuth callback rejected.') } }
      const aliases = [`localhost:${url.port}`, `127.0.0.1:${url.port}`, `[::1]:${url.port}`]
      if (!receiver.state || receiver.finished || request.url.length > 16384 || !aliases.includes(request.headers.host) ||
          request.method !== (receiver.mode === 'form_post' ? 'POST' : 'GET')) return fail(400)
      let callback, fields
      try {
        callback = new URL(request.url, url.origin)
        if (callback.origin !== url.origin || callback.pathname !== url.pathname) return fail(400)
        if (request.method === 'POST') {
          if (request.headers['content-type']?.split(';')[0].trim() !== 'application/x-www-form-urlencoded' ||
              request.headers['transfer-encoding'] || callback.search ||
              !/^\d+$/.test(request.headers['content-length'] || '') || Number(request.headers['content-length']) > LIMIT) return fail(400)
          const chunks = []
          let size = 0
          for await (const chunk of request) {
            size += chunk.length
            if (size > LIMIT) return fail(413)
            chunks.push(chunk)
          }
          fields = callbackFields(new URLSearchParams(Buffer.concat(chunks).toString('utf8')), receiver.state)
        } else fields = callbackFields(callback.searchParams, receiver.state)
      } catch { return fail(400) }
      // Multiple slow requests may have read concurrently. Consume exactly one.
      if (receiver.finished || !receivers.has(owner)) return fail(400)
      receiver.finished = true
      let link = ''
      try { link = returnURL(owner) } catch { /* Native focus can still return to the owner. */ }
      const page = callbackPage(link)
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
        'Content-Security-Policy': `default-src 'none'; script-src 'nonce-${page.nonce}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`,
        'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff' })
      response.once('finish', () => {
        try { onReceived(owner) } catch { /* OS focus restrictions must not lose the callback. */ }
        cancel(owner)
      })
      const resolve = receiver.resolve
      receiver.resolve = receiver.reject = null
      response.end(page.html)
      resolve({ method: request.method, fields })
    }
    async function listen (host, port) {
      const server = http.createServer({ maxHeaderSize: 16384 }, (request, response) => {
        handle(request, response).catch(() => { response.destroy() })
      })
      server.requestTimeout = server.headersTimeout = 10000
      receiver.servers.push(server)
      await new Promise((resolve, reject) => {
        server.once('error', reject)
        server.listen({ port, host, ipv6Only: true }, () => {
          server.removeListener('error', reject)
          server.on('error', () => cancel(owner))
          resolve()
        })
      })
      return server.address().port
    }
    try {
      const originalPort = Number(url.port)
      url.port = String(await listen(url.hostname === '[::1]' ? '::1' : '127.0.0.1', originalPort))
      if (url.hostname === 'localhost') {
        // Browsers may prefer IPv6 localhost. Never open login while another app
        // owns that interface/port; tolerate only platforms without IPv6 support.
        try { await listen('::1', Number(url.port)) } catch (error) {
          if (!['EAFNOSUPPORT', 'EADDRNOTAVAIL'].includes(error.code)) throw error
        }
      }
    } catch {
      cancel(owner)
      throw new Error(`Cannot open OAuth callback port ${url.port}; close the app using it and try again`)
    }
    if (!receivers.has(owner)) throw new Error('OAuth receiver cancelled')
    receiver.redirect = redirect.includes(':0') ? redirect.replace(':0', `:${url.port}`) : redirect
    receiver.timer = setTimeout(() => cancel(owner), timeout)
    receiver.timer.unref?.()
    return { id: receiver.id, redirect_uri: receiver.redirect, port: Number(url.port) }
  }

  async function authorize (owner, { id, authorization_url: value, state, response_mode: mode = 'query' }) {
    const receiver = receivers.get(owner)
    if (!receiver || receiver.id !== id || receiver.state) throw new Error('No matching OAuth receiver')
    const url = new URL(value)
    if (typeof value !== 'string' || value.length > 16384 || url.protocol !== 'https:' ||
        url.username || url.password || url.hash || /[\s\\]/.test(value) ||
        typeof state !== 'string' || state.length < 16 || state.length > 8192 || /[^\x20-\x7e]/.test(state) ||
        url.searchParams.getAll('state').length !== 1 || url.searchParams.get('state') !== state ||
        url.searchParams.getAll('redirect_uri').length !== 1 || url.searchParams.get('redirect_uri') !== receiver.redirect ||
        url.searchParams.getAll('response_mode').length > 1 || !['query', 'form_post'].includes(mode) ||
        (url.searchParams.get('response_mode') || 'query') !== mode ||
        url.searchParams.get('code_challenge_method') !== 'S256' ||
        !/^[A-Za-z0-9_-]{43}$/.test(url.searchParams.get('code_challenge') || '') ||
        url.searchParams.get('response_type') !== 'code' ||
        ['access_token', 'refresh_token', 'client_secret', 'code', 'id_token'].some(key => url.searchParams.has(key))) {
      throw new Error('Invalid OAuth authorization URL')
    }
    receiver.state = state
    receiver.mode = mode
    return new Promise((resolve, reject) => {
      receiver.resolve = resolve
      receiver.reject = reject
      Promise.resolve().then(() => openExternal(value)).catch(() => cancel(owner))
    })
  }

  return { prepare, authorize, cancel, close: () => { for (const owner of receivers.keys()) cancel(owner) } }
}

module.exports = { createReceivers, redirectURL }
