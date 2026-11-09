import { ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { actions } from './store'
import { chatLocation } from './router'

export function useChatBranch (getChat) {
  const busy = ref(false)
  const route = useRoute()
  const router = useRouter()
  const $q = useQuasar()

  async function branch () {
    const chat = getChat()
    if (!chat || busy.value) return
    const origin = route.fullPath
    busy.value = true
    try {
      const created = await api.branchChat(chat.id)
      await actions.loadChats()
      // A slow copy must not pull the user away from a different conversation.
      if (route.fullPath === origin) await router.push(chatLocation(created.id))
      $q.notify({ type: 'positive', message: 'Chat branched. Explore a different direction here.' })
    } catch (error) {
      $q.notify({ type: 'negative', message: error.message })
    } finally {
      busy.value = false
    }
  }
  return { branch, branchBusy: busy }
}
