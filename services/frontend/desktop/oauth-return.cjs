// Focus-only OS links. OAuth codes/state/tokens never enter a custom protocol.
const crypto = require('node:crypto')
const { trustedRenderer } = require('./security.cjs')
function createFocusOwner ({ BrowserWindow, app, platform = process.platform }) {
  return owner => {
    const window = BrowserWindow.getAllWindows().find(item => !item.isDestroyed() &&
      !item.webContents.isDestroyed() && item.webContents.id === owner && trustedRenderer(item.webContents.getURL()))
    if (!window) return false
    if (window.isMinimized()) window.restore()
    window.show()
    if (platform === 'darwin') app.focus({ steal: true })
    window.focus()
    if (!window.isFocused()) {
      window.flashFrame(true)
      window.once('focus', () => { if (!window.isDestroyed()) window.flashFrame(false) })
    }
    return true
  }
}
const PREFIX = 'nautionette://oauth-return/'

function createAppReturns ({ focus, now = Date.now, lifetime = 120000 }) {
  const pending = new Map()
  function prune () {
    for (const [token, item] of pending) if (item.until <= now()) pending.delete(token)
  }
  function clear (owner) {
    for (const [token, item] of pending) if (item.owner === owner) pending.delete(token)
  }
  function issue (owner) {
    prune()
    clear(owner)
    const token = crypto.randomBytes(32).toString('hex')
    pending.set(token, { owner, until: now() + lifetime })
    return PREFIX + token
  }
  function foreground (owner) {
    try { return Boolean(focus(owner)) } catch { return false }
  }
  function activate (value) {
    prune()
    // Strict spelling also rejects query strings, credentials, paths and ports.
    if (typeof value !== 'string' || !/^nautionette:\/\/oauth-return\/[a-f0-9]{64}$/.test(value)) return false
    const token = value.slice(PREFIX.length), item = pending.get(token)
    if (!item) return false
    pending.delete(token)
    return foreground(item.owner)
  }
  return { issue, foreground, activate, clear, close: () => pending.clear() }
}
module.exports = { createAppReturns, createFocusOwner }
