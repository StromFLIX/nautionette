const { contextBridge, ipcRenderer } = require('electron')
// No general IPC/network/filesystem access. Codes are returned transiently to
// the bundled renderer, which forwards them through its authenticated API.
// Read-only OS chrome state; no window-control IPC is exposed to web content.
ipcRenderer.on('window:fullscreen', (_event, fullscreen) => {
  document.documentElement.classList.toggle('desktop-fullscreen', fullscreen === true)
})
const bridge = {
  windowControlsOverlay: process.platform !== 'darwin',
  oauth: {
    version: 1,
    prepare: redirect => ipcRenderer.invoke('oauth:prepare', redirect),
    authorize: payload => ipcRenderer.invoke('oauth:authorize', payload),
    cancel: () => ipcRenderer.invoke('oauth:cancel')
  }
}
contextBridge.exposeInMainWorld('denkzeugDesktop', bridge)
// Older installed renderers still use the same narrow, authenticated IPC API.
contextBridge.exposeInMainWorld('nautionetteDesktop', bridge)
