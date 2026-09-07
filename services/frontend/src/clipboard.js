// Clipboard API needs a secure context and can be denied by the browser.
// Retain a selection-based fallback for HTTP/self-hosted and mobile clients.
export async function copyText (text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return
    }
  } catch {
    // Try the legacy user-gesture copy path before reporting a failure.
  }

  const active = document.activeElement
  const selection = window.getSelection()
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : []
  const field = document.createElement('textarea')
  field.value = text
  field.readOnly = true
  field.style.cssText = 'position:fixed;left:-9999px;top:0;font-size:16px;'
  document.body.append(field)
  try {
    field.focus({ preventScroll: true })
    field.select()
    if (!document.execCommand('copy')) throw new Error('Clipboard unavailable')
  } finally {
    field.remove()
    active?.focus({ preventScroll: true })
    if (selection) {
      selection.removeAllRanges()
      for (const range of ranges) selection.addRange(range)
    }
  }
}
