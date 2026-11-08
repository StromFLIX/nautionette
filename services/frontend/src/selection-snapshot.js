import { onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'

// Keep the selected DOM intact, including when a live answer becomes a saved
// message. Only presentation is held; the stream and operational state continue.
export function useSelectionSnapshot (root, source, identity) {
  const snapshot = shallowRef(source.value)
  const paused = ref(false)
  let dragging = false

  function hasSelection () {
    const selection = window.getSelection()
    if (!root.value || !selection || selection.isCollapsed) return false
    for (let index = 0; index < selection.rangeCount; index++) {
      if (selection.getRangeAt(index).intersectsNode(root.value)) return true
    }
    return false
  }

  function refresh () {
    paused.value = dragging || hasSelection()
    if (!paused.value) snapshot.value = source.value
  }

  function pointerDown (event) {
    // Protect the first drag/long-press frame, before selectionchange. Native
    // touch scrolling releases the hold via pointercancel; controls stay usable.
    if (event.button !== 0 || !root.value?.contains(event.target) ||
        event.target.closest('button, a, input, textarea, select')) return
    dragging = true
    refresh()
  }
  function pointerUp () {
    dragging = false
    refresh()
  }

  watch(source, () => { if (!paused.value) snapshot.value = source.value }, { flush: 'sync' })
  watch(identity, () => {
    dragging = false
    paused.value = false
    snapshot.value = source.value
  }, { flush: 'sync' })

  onMounted(() => {
    document.addEventListener('pointerdown', pointerDown, true)
    document.addEventListener('pointerup', pointerUp, true)
    document.addEventListener('pointercancel', pointerUp, true)
    document.addEventListener('selectionchange', refresh)
    window.addEventListener('blur', pointerUp)
  })
  onUnmounted(() => {
    document.removeEventListener('pointerdown', pointerDown, true)
    document.removeEventListener('pointerup', pointerUp, true)
    document.removeEventListener('pointercancel', pointerUp, true)
    document.removeEventListener('selectionchange', refresh)
    window.removeEventListener('blur', pointerUp)
  })

  return { snapshot, paused }
}
