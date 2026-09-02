// Android 15 draws the keyboard over the web view instead of shrinking it, and a
// plain browser only shrinks the visual viewport. Both cases reduce to one CSS
// length the layout can subtract: --keyboard-inset.
import { isNative } from './api'

const root = document.documentElement

let reported = 0
let unobscured = window.innerHeight
let width = window.innerWidth
let applied = -1

function apply () {
  const viewport = window.visualViewport
  const visible = viewport ? viewport.height + viewport.offsetTop : window.innerHeight
  // Whatever the platform already took off the viewport is not ours to take again.
  const resized = Math.max(0, unobscured - window.innerHeight)
  const inset = Math.round(Math.max(0, window.innerHeight - visible, reported - resized))
  if (inset === applied) return

  const grew = applied >= 0 && inset > applied
  applied = inset
  root.style.setProperty('--keyboard-inset', `${inset}px`)
  if (grew) {
    requestAnimationFrame(() => document.activeElement?.scrollIntoView?.({ block: 'nearest' }))
  }
}

function onResize () {
  // The tallest the window gets at this width is the window without a keyboard.
  if (window.innerWidth !== width) {
    width = window.innerWidth
    unobscured = window.innerHeight
  } else {
    unobscured = Math.max(unobscured, window.innerHeight)
  }
  apply()
}

export function watchKeyboard () {
  window.addEventListener('resize', onResize)
  window.visualViewport?.addEventListener('resize', apply)
  window.visualViewport?.addEventListener('scroll', apply)

  if (!isNative) return
  import('@capacitor/keyboard').then(({ Keyboard }) => {
    const show = (info) => { reported = info.keyboardHeight; apply() }
    const hide = () => { reported = 0; apply() }
    Keyboard.addListener('keyboardWillShow', show)
    Keyboard.addListener('keyboardDidShow', show)
    Keyboard.addListener('keyboardWillHide', hide)
    Keyboard.addListener('keyboardDidHide', hide)
  })
}
