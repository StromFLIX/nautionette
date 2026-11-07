const { contextBridge } = require('electron')
// A marker only: the web app still uses authenticated HTTP APIs directly.
contextBridge.exposeInMainWorld('nautionetteDesktop', true)
