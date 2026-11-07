const path = require('node:path')

const APP_ORIGIN = 'capacitor://localhost'
function trustedRenderer (url) {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'capacitor:' && parsed.host === 'localhost'
  } catch { return false }
}
function externalURL (url) {
  try { return ['https:', 'http:'].includes(new URL(url).protocol) } catch { return false }
}
function assetPath (root, url) {
  if (!trustedRenderer(url)) return null
  try {
    const pathname = decodeURIComponent(new URL(url).pathname)
    if (pathname.includes('\0') || pathname.includes('\\')) return null
    const file = path.resolve(root, `.${pathname === '/' ? '/index.html' : pathname}`)
    return file.startsWith(`${root}${path.sep}`) ? file : null
  } catch { return null }
}
module.exports = { APP_ORIGIN, trustedRenderer, externalURL, assetPath }
