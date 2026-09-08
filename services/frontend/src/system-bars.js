import { Capacitor, registerPlugin } from '@capacitor/core'
import { contrastRatio, themeById } from './themes.js'

const SystemBars = registerPlugin('NautionetteSystemBars')

// Theme overrides use CSS #RRGGBBAA; Android expects #AARRGGBB. Composite
// translucent surfaces onto the preset instead of passing ambiguous hex values.
function opaque (color, background) {
  const alpha = color.length === 9 ? parseInt(color.slice(7), 16) / 255 : 1
  return '#' + [1, 3, 5].map(index => Math.round(
    parseInt(color.slice(index, index + 2), 16) * alpha +
    parseInt(background.slice(index, index + 2), 16) * (1 - alpha)
  ).toString(16).padStart(2, '0')).join('')
}

export function systemBarAppearance (themeId, tokens, mobileList) {
  const preset = themeById(themeId).colors
  const statusToken = mobileList ? 'surface-panel' : 'surface-app'
  const navigationToken = mobileList ? 'surface-rail' : 'surface-app'
  const statusBarColor = opaque(tokens[statusToken], preset[statusToken])
  const navigationBarColor = opaque(tokens[navigationToken], preset[navigationToken])
  const darkIcons = color => contrastRatio('#000000', color) > contrastRatio('#ffffff', color)
  return {
    statusBarColor,
    navigationBarColor,
    darkStatusBarIcons: darkIcons(statusBarColor),
    darkNavigationBarIcons: darkIcons(navigationBarColor)
  }
}

let pending = Promise.resolve()
let lastAppearance = ''

export function syncSystemBars (themeId, tokens, mobileList) {
  if (Capacitor.getPlatform() !== 'android') return
  const appearance = systemBarAppearance(themeId, tokens, mobileList)
  const key = JSON.stringify(appearance)
  if (key === lastAppearance) return pending
  lastAppearance = key
  // Keep rapid edits in order. A missing/older native bridge must not break the UI.
  pending = pending.then(() => SystemBars.setAppearance(appearance)).catch(error => {
    lastAppearance = ''
    console.warn('Android system bar colors could not be updated.', error)
  })
  return pending
}
