import { onScopeDispose, reactive, ref, watch } from 'vue'

/** Owned by the app shell so leaving for Settings does not lose the grace period. */
export function useChatActivity (selectedChatId, preferences) {
  const now = ref(Date.now())
  // Viewing a chat is local UI state, not new server-side activity or unread state.
  const leftAt = reactive(new Map())
  let expiryTimer = null

  function refresh () {
    clearTimeout(expiryTimer)
    expiryTimer = null
    now.value = Date.now()
    const grace = preferences.chatKeepSelectedVisible ? preferences.chatSelectionGraceSeconds * 1000 : 0
    let nextExpiry = Infinity
    for (const [id, timestamp] of leftAt) {
      const expiry = timestamp + grace
      if (!grace || expiry <= now.value) leftAt.delete(id)
      else nextExpiry = Math.min(nextExpiry, expiry)
    }
    // Expire at the actual deadline, not up to 30 seconds after it on the activity tick.
    if (Number.isFinite(nextExpiry)) expiryTimer = setTimeout(refresh, nextExpiry - now.value)
  }

  watch(selectedChatId, (id, previousId) => {
    if (previousId && preferences.chatKeepSelectedVisible && preferences.chatSelectionGraceSeconds > 0) {
      leftAt.set(previousId, Date.now())
    }
    leftAt.delete(id)
    refresh()
  }, { flush: 'sync' })
  watch([() => preferences.chatKeepSelectedVisible, () => preferences.chatSelectionGraceSeconds], refresh, { flush: 'sync' })

  // Relative activity windows still need to age when no chat or server state changes.
  const ticker = setInterval(refresh, 30000)
  onScopeDispose(() => { clearInterval(ticker); clearTimeout(expiryTimer) })

  return function isChatActive (chat) {
    if (chat.unread || chat.answering || ['pending', 'deciding'].includes(chat.internet_status)) return true
    if (preferences.chatKeepSelectedVisible) {
      if (chat.id === selectedChatId.value) return true
      const timestamp = leftAt.get(chat.id)
      if (timestamp !== undefined && now.value < timestamp + preferences.chatSelectionGraceSeconds * 1000) return true
    }
    if (!preferences.chatActiveMinutes) return true
    if (!chat.updated_at) return false
    return now.value / 1000 - chat.updated_at <= preferences.chatActiveMinutes * 60
  }
}
