<template>
  <ChatWelcome v-if="!chatId" :busy="starting" @start="start" />

  <div v-else class="thread stack grow">
    <header class="pane-head">
      <button class="btn btn--icon pane-head__back" @click="backTo('/chats')">
        <span class="material-icons">arrow_back</span>
        <span v-if="draftCount" class="pane-head__badge">{{ draftCount }}</span>
      </button>
      <div class="avatar-sm" :style="avatarStyle(chatId)">{{ initials(chat?.title || '?') }}</div>
      <div class="grow">
        <div class="pane-head__title truncate">{{ chat?.title || 'Chat' }}</div>
        <div class="caption dim truncate">
          {{ messages.length }} messages · {{ chat?.agent_set }}
          <template v-if="reconnecting"> · Reconnecting...</template>
          <template v-if="chat?.promoted_to"> · → {{ chat.promoted_to }}</template>
        </div>
      </div>
      <span v-if="chat?.internet_status === 'allowed'" class="material-icons internet-indicator" role="img" aria-label="Internet allowed for this chat">
        public
        <q-tooltip>Internet allowed for this chat</q-tooltip>
      </span>
      <RouterLink v-if="chat?.promoted_to" class="btn btn--outline btn--sm" :to="`/workflows/${chat.promoted_to}`">
        <span class="material-icons" style="font-size: 15px">account_tree</span>
        {{ chat.promoted_to }}
      </RouterLink>
      <button class="btn btn--icon">
        <span class="material-icons">more_vert</span>
        <q-menu anchor="bottom right" self="top right" class="pick-menu">
          <button class="pick-menu__item" @click="rename">
            <span class="material-icons pick__icon">edit</span>Rename
          </button>
          <button class="pick-menu__item" @click="remove">
            <span class="material-icons pick__icon" style="color: var(--danger)">delete</span>Delete chat
          </button>
        </q-menu>
      </button>
    </header>

    <div ref="scroller" class="thread__body scroll-y grow">
      <div class="thread__inner">
        <MessageBubble
          v-for="message in messages" :key="message.id"
          :role="message.role" :content="message.content"
          :meta="message.meta" :created-at="message.created_at"
          :delivery-state="message.delivery || ''" :delivery-error="message.deliveryError || ''"
          @retry="delivery.retry(message.id)" @discard="delivery.discard(message.id)"
        />
        <MessageBubble
          v-if="streaming" role="assistant" live
          :content="liveSteps.length || liveStatus ? '' : '…'" :meta="{ steps: liveSteps }"
          :status="liveStatus"
        />
      </div>
    </div>

    <div class="thread__foot">
      <section v-if="internetPending" class="thread__approval" aria-label="Internet access request" aria-live="polite">
        <div class="thread__approval-title">Allow internet for this chat?</div>
        <p class="thread__approval-reason">{{ chat.internet_reason }}</p>
        <div class="thread__approval-actions">
          <button class="btn btn--outline btn--sm" :disabled="approvalBusy" @click="decideInternet(false)">
            <span class="material-icons" aria-hidden="true">block</span>Deny
          </button>
          <button class="btn btn--primary btn--sm" :disabled="approvalBusy" @click="decideInternet(true)">
            <span class="material-icons" aria-hidden="true">public</span>Allow for this chat
          </button>
        </div>
        <p v-if="approvalError" class="thread__approval-error" role="alert">{{ approvalError }}</p>
      </section>
      <Composer
        ref="composer"
        v-model="draft"
        :agent-set="chat?.agent_set || ''"
        :model="chat?.model || store.catalog.default_model"
        :tools="chat?.tools ?? null"
        :busy="streaming"
        :context-used="contextUsed"
        @update:agent-set="patch({ agent_set: $event })"
        @update:model="patch({ model: $event })"
        @update:tools="patch({ tools: $event })"
        @send="send"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { isNavigationFailure, NavigationFailureType, useRoute, useRouter } from 'vue-router'
import ChatWelcome from '../components/ChatWelcome.vue'
import Composer from '../components/Composer.vue'
import MessageBubble from '../components/MessageBubble.vue'
import { avatarStyle, initials } from '../format'
import { backTo } from '../router'
import { actions, draftCount, onLiveEvent, store } from '../store'
import { api, chatStream } from '../api'
import { delivery, onDelivery, pendingMessages } from '../delivery'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()

const chat = ref(null)
const savedMessages = ref([])
const draft = ref('')
const activeTurn = ref(null)
const reconnecting = ref(false)
const streaming = computed(() => Boolean(activeTurn.value))
const liveSteps = computed(() => activeTurn.value?.steps || [])
const liveStatus = computed(() => activeTurn.value?.status || '')
const starting = ref(false)
const scroller = ref(null)
const composer = ref(null)
const approvalRequest = ref('')
const approvalError = ref('')
const internetPending = computed(() => ['pending', 'deciding'].includes(chat.value?.internet_status))
const approvalBusy = computed(() => approvalRequest.value === chatId.value || chat.value?.internet_status === 'deciding')

const chatId = computed(() => route.params.id || '')
const messages = computed(() => {
  const known = new Set(savedMessages.value.map((message) => message.id))
  const pending = pendingMessages.value.filter((item) => item.chatId === chatId.value && !known.has(item.id))
  return [...savedMessages.value, ...pending.map((item) => ({
    id: item.id, role: 'user', content: item.text, created_at: item.createdAt / 1000,
    delivery: item.error ? 'failed' : 'sending', deliveryError: item.error
  }))]
})
const contextUsed = computed(() =>
  messages.value.reduce((total, message) => total + (message.content || '').length, 0))

let stream = null
let generation = 0

function applySnapshot (data) {
  const el = scroller.value
  const atBottom = !savedMessages.value.length || !el || el.scrollHeight - el.scrollTop - el.clientHeight < 100
  chat.value = data.chat
  savedMessages.value = data.messages
  activeTurn.value = data.active_turn || null
  delivery.reconcile(data.messages)
  reconnecting.value = false
  if (atBottom) scrollDown('instant')
}

function connectChat () {
  stream?.close()
  stream = null
  const version = ++generation
  const id = chatId.value
  if (!id) return
  reconnecting.value = true
  let received = false
  api.chat(id).then((data) => {
    if (version === generation && !received) applySnapshot(data)
  }).catch((error) => {
    if (version !== generation) return
    if (error.status === 401) store.needsToken = true
    if (error.status === 404) router.replace('/chats')
  })
  stream = chatStream(id, (data) => {
    if (version !== generation) return
    received = true
    if (!data.chat) {
      stream?.close()
      router.replace('/chats')
      return
    }
    applySnapshot(data)
  }, () => { if (version === generation) reconnecting.value = true })
}

function scrollDown (behavior = 'smooth') {
  nextTick(() => {
    const el = scroller.value
    if (el) el.scrollTo({ top: el.scrollHeight, behavior })
  })
}

async function start ({ text, agentSet, model, tools }) {
  if (!text.trim()) return
  starting.value = true
  try {
    const created = await api.createChat({ agent_set: agentSet, model, tools })
    await actions.loadChats()
    await router.push(`/chats/${created.id}`)
    draft.value = text
    await send()
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    starting.value = false
  }
}

function send () {
  const text = draft.value.trim()
  if (!text || streaming.value) return
  try {
    delivery.enqueue(chatId.value, text)
    draft.value = ''
    scrollDown()
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  }
  nextTick(() => composer.value?.focus())
}

async function patch (fields) {
  chat.value = await api.updateChat(chatId.value, fields)
  actions.loadChats()
}

async function decideInternet (allowed) {
  const id = chatId.value
  const turnId = chat.value?.internet_turn_id
  if (!turnId || approvalBusy.value) return
  approvalRequest.value = id
  approvalError.value = ''
  try {
    const updated = await api.decideInternet(id, turnId, allowed)
    if (chatId.value === id) chat.value = updated
  } catch (error) {
    if (chatId.value === id) approvalError.value = error.message
  } finally {
    if (approvalRequest.value === id) approvalRequest.value = ''
  }
}

function rename () {
  $q.dialog({
    title: 'Rename chat',
    prompt: { model: chat.value?.title || '', type: 'text', outlined: true, dark: true },
    cancel: true
  }).onOk((title) => patch({ title }))
}

function remove () {
  $q.dialog({ title: 'Delete chat', message: `Delete “${chat.value?.title}”?`, cancel: true })
    .onOk(async () => {
      await api.deleteChat(chatId.value)
      await actions.loadChats()
      router.push('/chats')
    })
}

watch(chatId, (id) => {
  approvalError.value = ''
  chat.value = null
  savedMessages.value = []
  activeTurn.value = null
  draft.value = ''
  connectChat()
})

const stopNavigation = router.afterEach((to, from, failure) => {
  if (to.name === 'chats' && to.params.id === chatId.value &&
      isNavigationFailure(failure, NavigationFailureType.duplicated)) {
    scrollDown('instant')
  }
})
onUnmounted(stopNavigation)

let off = () => {}
let offDelivery = () => {}
function resume () {
  if (document.visibilityState === 'visible') connectChat()
}
onMounted(() => {
  connectChat()
  off = onLiveEvent((event) => {
    if (event.kind === 'client.reconnect') connectChat()
  })
  offDelivery = onDelivery((message) => {
    if (message.chat_id === chatId.value && !savedMessages.value.some((saved) => saved.id === message.id)) {
      savedMessages.value.push(message)
      scrollDown()
    }
  })
  window.addEventListener('online', connectChat)
  document.addEventListener('visibilitychange', resume)
})
onUnmounted(() => {
  generation++
  stream?.close()
  off()
  offDelivery()
  window.removeEventListener('online', connectChat)
  document.removeEventListener('visibilitychange', resume)
})
</script>

<style scoped>
.thread {
  min-width: 0;
  overflow: hidden;
}

.thread__body {
  padding: 18px 0;
}

.thread__inner {
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  min-height: 100%;
  max-width: 900px;
  margin: 0 auto;
  padding: 0 24px;
}

.thread__hint {
  padding: 40px 0;
  text-align: center;
}

.thread__foot {
  padding: 10px 24px 14px;
  border-top: 1px solid var(--border);
  background: var(--surface-app);
}

.thread__foot > :deep(.composer) {
  max-width: 900px;
  margin: 0 auto;
}

.thread__approval {
  max-width: 900px;
  margin: 0 auto 10px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}

.thread__approval-title {
  font-size: 14px;
  font-weight: 650;
}

.thread__approval-reason {
  max-height: 80px;
  overflow-y: auto;
  overflow-wrap: anywhere;
  margin: 4px 0 10px;
  font-size: 13px;
  color: var(--text-muted);
}

.thread__approval-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.thread__approval-actions .material-icons {
  font-size: 16px;
}

.thread__approval-error {
  margin: 8px 0 0;
  color: var(--danger);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.internet-indicator {
  flex: none;
  font-size: 18px;
  color: var(--success);
}

.avatar-sm {
  display: grid;
  place-items: center;
  flex: none;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  color: #fff;
  font-size: 12px;
  font-weight: 650;
}

.pane-head__back {
  display: none;
  position: relative;
}

/* Something is waiting back on the lists, without a nav bar to say so. */
.pane-head__badge {
  position: absolute;
  top: 2px;
  right: 2px;
  min-width: 15px;
  height: 15px;
  padding: 0 4px;
  border-radius: var(--radius-pill);
  background: var(--warning);
  color: #16181d;
  font-size: 10px;
  font-weight: 700;
  line-height: 15px;
  text-align: center;
}

@media (max-width: 900px) {
  .pane-head__back {
    display: grid;
  }

  .thread__body {
    padding-top: 12px;
    padding-bottom: 12px;
  }

  .thread__inner {
    padding-right: max(12px, env(safe-area-inset-right));
    padding-left: max(12px, env(safe-area-inset-left));
  }

  .thread__foot {
    padding: 10px max(12px, env(safe-area-inset-right)) 10px max(12px, env(safe-area-inset-left));
  }
}

@media (max-width: 420px) {
  .avatar-sm {
    width: 32px;
    height: 32px;
  }

  .pane-head > .btn--outline {
    width: 36px;
    padding: 0;
    overflow: hidden;
    font-size: 0;
  }

  .pane-head > .btn--outline .material-icons {
    font-size: 17px !important;
  }

  .thread__inner {
    padding-right: max(10px, env(safe-area-inset-right));
    padding-left: max(10px, env(safe-area-inset-left));
  }

  .thread__foot {
    padding-right: max(8px, env(safe-area-inset-right));
    padding-left: max(8px, env(safe-area-inset-left));
  }
}
</style>
