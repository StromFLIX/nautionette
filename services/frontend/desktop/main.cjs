const { app, BrowserWindow, protocol, net, session, shell, ipcMain } = require('electron')
const path = require('node:path')
const brand = require('./brand.json')
const { existsSync, mkdirSync } = require('node:fs')
const { pathToFileURL } = require('node:url')
const { APP_ORIGIN, trustedRenderer, externalURL, assetPath } = require('./security.cjs')
const { existingDataPath } = require('./data-path.cjs')

// Pin identity before acquiring the single-instance lock or opening a session.
const dataPath = existingDataPath(app.getPath('appData'), app.getPath('userData'))
mkdirSync(dataPath, { recursive: true })
app.setPath('userData', dataPath)
app.setPath('sessionData', dataPath)
app.setName(brand.name)

const { createReceivers } = require('./oauth.cjs')
const { createAppReturns, createFocusOwner } = require('./oauth-return.cjs')
let returnProtocol = false
const focusOwner = createFocusOwner({ BrowserWindow, app })
const appReturns = createAppReturns({ focus: focusOwner })
const oauth = createReceivers({ openExternal: url => shell.openExternal(url),
  onReceived: owner => appReturns.foreground(owner),
  returnURL: owner => returnProtocol ? appReturns.issue(owner) : '' })
const primaryInstance = app.requestSingleInstanceLock()
if (!primaryInstance) app.quit()
else {
  app.on('second-instance', (_event, argv) => {
    const link = argv.find(value => typeof value === 'string' && value.startsWith('nautionette:'))
    if (link) appReturns.activate(link)
    else {
      const window = BrowserWindow.getAllWindows().find(item => !item.isDestroyed())
      if (window) focusOwner(window.webContents.id)
    }
  })
  app.on('open-url', (event, url) => { event.preventDefault(); appReturns.activate(url) })
}

// Only the bundled top-level renderer can use the narrow OAuth bridge.
function oauthOwner (event) {
  if (event.senderFrame !== event.sender.mainFrame || !trustedRenderer(event.senderFrame.url) ||
      !trustedRenderer(event.sender.getURL())) throw new Error('Untrusted OAuth caller')
  return event.sender.id
}
ipcMain.handle('oauth:prepare', (event, redirect) => oauth.prepare(oauthOwner(event), redirect))
ipcMain.handle('oauth:authorize', (event, payload) => oauth.authorize(oauthOwner(event), payload))
ipcMain.handle('oauth:cancel', event => oauth.cancel(oauthOwner(event)))
app.on('before-quit', () => { oauth.close(); appReturns.close() })

// Match the existing Capacitor origin allowed by the backend; no Node integration
// or arbitrary IPC is exposed to the renderer.
protocol.registerSchemesAsPrivileged([
  { scheme: 'capacitor', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } }
])

function createWindow () {
  const window = new BrowserWindow({
    width: 1280, height: 850, minWidth: 360, minHeight: 500,
    title: brand.name, backgroundColor: brand.colors.paper,
    icon: path.join(__dirname, 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false, contextIsolation: true, sandbox: true,
      webSecurity: true
    }
  })
  const owner = window.webContents.id
  window.webContents.once('destroyed', () => { oauth.cancel(owner); appReturns.clear(owner) })
  window.webContents.on('did-start-navigation', (_event, _url, _inPlace, isMainFrame) => {
    if (isMainFrame) { oauth.cancel(owner); appReturns.clear(owner) }
  })
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (externalURL(url)) shell.openExternal(url).catch(() => {})
    return { action: 'deny' }
  })
  // Remote pages and generated documents never replace the privileged app.
  window.webContents.on('will-navigate', (event, url) => {
    if (!trustedRenderer(url)) {
      event.preventDefault()
      if (externalURL(url)) shell.openExternal(url).catch(() => {})
    }
  })
  window.webContents.on('will-attach-webview', event => event.preventDefault())
  window.loadURL(`${APP_ORIGIN}/`)
}

if (primaryInstance) app.whenReady().then(() => {
  // Development must not replace an installed app's OS handler. Packaged
  // installers declare this protocol too; the URL can only focus an owner.
  if (app.isPackaged) returnProtocol = app.setAsDefaultProtocolClient('nautionette') || app.isDefaultProtocolClient('nautionette')
  const root = path.resolve(__dirname, '../dist')
  protocol.handle('capacitor', request => {
    const file = assetPath(root, request.url)
    if (!file) return new Response('Not found', { status: 404 })
    if (existsSync(file)) return net.fetch(pathToFileURL(file).href)
    // Vue history routes must survive reloads, but missing JS/API requests
    // must not receive HTML in place of their actual resource.
    if (request.headers.get('accept')?.includes('text/html')) {
      return net.fetch(pathToFileURL(path.join(root, 'index.html')).href)
    }
    return new Response('Not found', { status: 404 })
  })
  session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) => {
    callback(Boolean(contents && trustedRenderer(contents.getURL()) &&
      trustedRenderer(details.requestingUrl) && permission === 'media' &&
      details.mediaTypes?.length && details.mediaTypes.every(type => type === 'audio')))
  })
  session.defaultSession.setPermissionCheckHandler((contents, permission, origin, details) =>
    Boolean(contents && trustedRenderer(contents.getURL()) && trustedRenderer(origin) &&
      permission === 'media' && details.mediaType === 'audio'))
  createWindow()
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() })
})
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
