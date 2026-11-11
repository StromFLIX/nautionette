// Keep OS-owned caption buttons (including Windows Snap Layouts), resizing and
// window dragging; only the redundant title/icon and menu rows are removed.
const OVERLAY_HEIGHT = 32
function windowChrome (platform = process.platform) {
  return platform === 'darwin' ? {} : {
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#202020', symbolColor: '#eeeeee', height: OVERLAY_HEIGHT }
  }
}
module.exports = { windowChrome, OVERLAY_HEIGHT }
