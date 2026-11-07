const { contextBridge, ipcRenderer } = require('electron')
// No general IPC/network/filesystem access. Codes are returned transiently to
// the bundled renderer, which forwards them through its authenticated API.
contextBridge.exposeInMainWorld('nautionetteDesktop', {
  oauth: {
    version: 1,
    prepare: redirect => ipcRenderer.invoke('oauth:prepare', redirect),
    authorize: payload => ipcRenderer.invoke('oauth:authorize', payload),
    cancel: () => ipcRenderer.invoke('oauth:cancel')
  }
})
