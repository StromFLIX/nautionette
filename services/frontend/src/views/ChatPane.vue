<template>
  <ChatWelcome v-if="!chatId" :busy="starting" @start="start" />

  <div v-else class="thread stack grow">
    <header class="pane-head">
      <button class="btn btn--icon pane-head__back" aria-label="Back to chats" @click="backTo('/chats')">
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
      <button class="btn btn--icon" aria-label="Chat options">
        <span class="material-icons">more_vert</span>
        <q-menu anchor="bottom right" self="top right" class="pick-menu">
          <button v-close-popup class="pick-menu__item" @click="markUnread">
            <span class="material-icons" aria-hidden="true">mark_email_unread</span>Mark as unread
          </button>
          <button class="pick-menu__item" @click="rename">
            <span class="material-icons pick__icon">edit</span>Rename
          </button>
          <button v-close-popup class="pick-menu__item" :disabled="titleBusy || !savedMessages.length" @click="regenerateTitle">
            <span class="material-icons pick__icon" aria-hidden="true">autorenew</span>{{ titleBusy ? 'Regenerating title…' : 'Regenerate title' }}
          </button>
          <button class="pick-menu__item" @click="remove">
            <span class="material-icons pick__icon" style="color: var(--danger)">delete</span>Delete chat
          </button>
        </q-menu>
      </button>
    </header>

    <div ref="scroller" class="thread__body scroll-y grow" @scroll.passive="acknowledgeRead">
      <div class="thread__inner">
        <p v-if="!messages.length && !activeTurn" class="caption dim" role="status">
          {{ reconnecting ? 'Waiting for connection. No saved messages on this device.' : 'No messages yet.' }}
        </p>
        <MessageBubble
          v-for="message in messages" :key="message.id"
          :role="message.role" :content="message.content"
          :meta="message.meta" :created-at="message.created_at" :chat-id="chatId"
          :delivery-state="message.delivery || ''" :delivery-error="message.deliveryError || ''"
          @retry="delivery.retry(message.id)" @discard="delivery.discard(message.id)"
        />
        <MessageBubble
          v-if="streaming" role="assistant" live
          :content="liveSteps.length || liveStatus ? '' : '…'" :meta="{ steps: liveSteps }"
          :status="liveStatus"
        />
        <section v-if="queuedMessages.length" class="thread__queue" aria-label="Queued messages">
          <div class="thread__queue-heading caption dim" role="status">
            {{ chat?.queue_paused ? 'Queue paused' : 'Queued' }} · {{ queuedMessages.length }}
            <button v-if="chat?.queue_paused && !streaming" class="btn btn--icon" aria-label="Resume queued messages" :disabled="controlBusy" @click="resumeQueue">
              <span class="material-icons" aria-hidden="true">play_arrow</span>
              <q-tooltip>Resume queued messages</q-tooltip>
            </button>
          </div>
          <div v-for="message in queuedMessages" :key="message.id" class="thread__queued-message">
            <div>
              <p>{{ message.content }}</p>
              <div class="thread__queued-images">
                <ChatImage v-for="image in message.meta?.attachments || []" :key="image.id" :image="image" :chat-id="chatId" />
              </div>
            </div>
            <button v-if="!streaming" class="btn btn--icon" aria-label="Remove queued message" :disabled="controlBusy" @click="discardQueued(message.id)">
              <span class="material-icons" aria-hidden="true">close</span>
              <q-tooltip>Remove queued message</q-tooltip>
            </button>
          </div>
        </section>
      </div>
    </div>

    <div class="thread__foot scroll-y">
      <p v-if="cacheError" class="caption" role="alert">{{ cacheError }}</p>
      <p v-if="controlError" class="caption" role="alert">{{ controlError }}</p>
      <p v-if="readError" class="caption" role="alert">{{ readError }}</p>
      <button v-if="chat?.queue_paused && !streaming && !queuedMessages.length" class="btn btn--sm" :disabled="controlBusy" @click="resumeQueue">
        <span class="material-icons" aria-hidden="true">play_arrow</span>Resume chat
      </button>
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
      <ProjectChanges v-if="chat?.project_ids?.length" :key="chatId"
        :chat-id="chatId" :project-ids="chat.project_ids" :running="streaming" />
      <Composer
        ref="composer"
        v-model="draft"
        v-model:attachments="attachments"
        :busy="sendingDraft"
        :agent-set="chat?.agent_set || ''"
        :model="chat?.model || store.catalog.default_model"
        :reasoning-effort="chat?.reasoning_effort ?? null"
        :tools="chat?.tools ?? null"
        :project-ids="chat?.project_ids || []"
        :running="streaming"
        :stopping="stopping"
        :context="context"
        @update:agent-set="patch({ agent_set: $event })"
        @update:model="patch({ model: $event, reasoning_effort: null })"
        @update:reasoning-effort="patch({ reasoning_effort: $event })"
        @update:tools="patch({ tools: $event })"
        @update:project-ids="patch({ project_ids: $event })"
        @send="send"
        @stop="stopResponse"
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
import ProjectChanges from '../components/ProjectChanges.vue'
import MessageBubble from '../components/MessageBubble.vue'
import ChatImage from '../components/ChatImage.vue'
import { uploadImages } from '../attachments'
import { avatarStyle, initials } from '../format'
import { backTo } from '../router'
import { actions, draftCount, onLiveEvent, store } from '../store'
import { api, chatStream } from '../api'
import { delivery, onDelivery, pendingMessages } from '../delivery'
import { latestContext } from '../context'
import { cacheError, chatCache, chatCacheScope } from '../chat-cache'
import { reducedMotion } from '../preferences'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()

const chat = ref(null)
const savedMessages = ref([])
const draft = ref('')
const attachments = ref([])
const sendingDraft = ref(false)
const activeTurn = ref(null)
const reconnecting = ref(false)
const streaming = computed(() => Boolean(activeTurn.value))
const controlBusy = ref(false)
const controlError = ref('')
const titleBusy = ref(false)
const stopping = computed(() => controlBusy.value || Boolean(activeTurn.value?.stop_requested))
const liveSteps = computed(() => activeTurn.value?.steps || [])
const liveStatus = computed(() => activeTurn.value?.stop_requested ? 'Stopping...' : activeTurn.value?.status || '')
const starting = ref(false)
const scroller = ref(null)
const composer = ref(null)
const approvalRequest = ref('')
const approvalError = ref('')
const internetPending = computed(() => ['pending', 'deciding'].includes(chat.value?.internet_status))
const approvalBusy = computed(() => approvalRequest.value === chatId.value || chat.value?.internet_status === 'deciding')

const chatId = computed(() => route.params.id || '')
const queuedMessages = computed(() => savedMessages.value.filter((message) => message.meta?.queued))
const messages = computed(() => {
  const known = new Set(savedMessages.value.map((message) => message.id))
  const pending = pendingMessages.value.filter((item) => item.chatId === chatId.value && !known.has(item.id))
  return [...savedMessages.value.filter((message) => !message.meta?.queued), ...pending.map((item) => ({
    id: item.id, role: 'user', content: item.text, created_at: item.createdAt / 1000,
    meta: { attachments: item.attachments || [] },
    delivery: item.error ? 'failed' : 'sending', deliveryError: item.error
  }))]
})
const context = computed(() => latestContext(
  savedMessages.value, activeTurn.value, chat.value?.model || store.catalog.default_model
))

let stream = null
let generation = 0
let settingsSave = Promise.resolve(true)
let readKey = ''
let readSuspended = false
let clearManualOnOpen = true
const readError = ref('')

async function acknowledgeRead () {
  const el = scroller.value
  if (readSuspended || !chat.value || reconnecting.value || document.visibilityState !== 'visible' || !el ||
      el.scrollHeight - el.scrollTop - el.clientHeight > 100) return
  const id = chatId.value
  const revision = chat.value.read_revision
  if (!Number.isInteger(revision)) return
  const messageId = savedMessages.value.findLast((message) => message.role === 'assistant')?.id || null
  const key = JSON.stringify([id, messageId, revision])
  if (readKey === key) return
  readKey = key
  const clearManual = clearManualOnOpen
  clearManualOnOpen = false
  try {
    await api.updateChatReadState(id, { message_id: messageId, revision, clear_manual: clearManual })
    if (chatId.value === id) readError.value = ''
    await actions.loadChats()
  } catch (error) {
    if (readKey === key) {
      readKey = ''
      clearManualOnOpen = clearManual
      readError.value = `Could not save read status: ${error.message}`
    }
  }
}

async function markUnread () {
  const id = chatId.value
  // Keep the reminder until the next visit, rather than clearing it in this view.
  readSuspended = true
  try {
    await api.updateChatReadState(id, { unread: true })
    await router.push('/chats')
    await actions.loadChats()
  } catch (error) {
    readSuspended = false
    readError.value = `Could not mark chat as unread: ${error.message}`
  }
}

function applySnapshot (data, cached = false) {
  const el = scroller.value
  const atBottom = !savedMessages.value.length || !el || el.scrollHeight - el.scrollTop - el.clientHeight < 100
  chat.value = data.chat
  savedMessages.value = data.messages
  activeTurn.value = data.active_turn || null
  if (!cached) {
    chatCache.put(data)
    delivery.reconcile(data.messages)
    reconnecting.value = false
  }
  if (atBottom) scrollDown('instant')
  if (!cached) nextTick(acknowledgeRead)
}

function connectChat () {
  stream?.close()
  stream = null
  const version = ++generation
  const id = chatId.value
  if (!id) return
  reconnecting.value = true
  let received = false
  const scope = chatCacheScope()
  chatCache.get(id, scope).then((data) => {
    if (data && version === generation && !received && scope === chatCacheScope()) applySnapshot(data, true)
  })
  api.chat(id).then((data) => {
    if (version === generation && !received) {
      received = true
      applySnapshot(data)
    }
  }).catch((error) => {
    if (version !== generation) return
    if (error.status === 401) store.needsToken = true
    if (error.status === 404) {
      chatCache.remove(id, scope)
      router.replace('/chats')
    }
  })
  stream = chatStream(id, (data) => {
    if (version !== generation) return
    received = true
    if (!data.chat) {
      stream?.close()
      chatCache.remove(id, scope)
      router.replace('/chats')
      return
    }
    applySnapshot(data)
  }, () => { if (version === generation) reconnecting.value = true })
}

function scrollDown (behavior = 'smooth') {
  nextTick(() => {
    const el = scroller.value
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: reducedMotion() ? 'instant' : behavior })
  })
}

async function start ({ text, agentSet, model, reasoningEffort = null, tools, projectIds: selectedProjects = [], attachments: images = [] }) {
  if (starting.value || (!text.trim() && !images.length)) return
  starting.value = true
  try {
    const created = await api.createChat({ agent_set: agentSet, model, reasoning_effort: reasoningEffort, tools, project_ids: selectedProjects })
    await actions.loadChats()
    await router.push(`/chats/${created.id}`)
    draft.value = text
    attachments.value = images
    chat.value = chat.value || created
    await send()
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    starting.value = false
  }
}

async function send () {
  if (sendingDraft.value) return
  const id = chatId.value
  const version = generation
  const text = draft.value.trim()
  const images = attachments.value
  if (!text && !images.length) return
  sendingDraft.value = true
  try {
    if (!await settingsSave || version !== generation || id !== chatId.value) return
    const selectedProjects = [...(chat.value?.project_ids || [])]
    const uploaded = await uploadImages(id, images, api.uploadImage)
    if (version !== generation || id !== chatId.value) return
    delivery.enqueue(id, text, selectedProjects, uploaded)
    draft.value = ''
    attachments.value = []
    scrollDown()
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    if (version === generation) sendingDraft.value = false
  }
  nextTick(() => composer.value?.focus())
}

function patch (fields) {
  const id = chatId.value
  const version = generation
  settingsSave = settingsSave.then(async () => {
    try {
      const updated = await api.updateChat(id, fields)
      if (id === chatId.value && version === generation) chat.value = updated
      actions.loadChats()
      return true
    } catch (error) {
      $q.notify({ type: 'negative', message: error.message })
      return false
    }
  })
  return settingsSave
}

async function chatControl (operation) {
  const id = chatId.value
  const version = generation
  if (controlBusy.value) return
  controlBusy.value = true
  controlError.value = ''
  try {
    const data = await operation(id)
    if (version === generation) applySnapshot(data)
  } catch (error) {
    if (version === generation) controlError.value = error.message
  } finally {
    if (version === generation) controlBusy.value = false
  }
}

function stopResponse () {
  const turnId = activeTurn.value?.id
  if (turnId) chatControl((id) => api.stopChat(id, turnId))
}

function resumeQueue () {
  chatControl((id) => api.resumeChatQueue(id))
}

function discardQueued (messageId) {
  chatControl((id) => api.discardQueuedMessage(id, messageId))
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

async function regenerateTitle () {
  if (titleBusy.value) return
  const id = chatId.value
  const version = generation
  titleBusy.value = true
  try {
    // Respect any settings/rename already being saved before generating a new title.
    if (!await settingsSave || version !== generation) return
    const updated = await api.regenerateChatTitle(id)
    if (version === generation) {
      // Don't replace unrelated settings that may have changed during generation.
      chat.value = { ...chat.value, title: updated.title }
      $q.notify({ type: 'positive', message: 'Chat title regenerated' })
    }
    await actions.loadChats()
  } catch (error) {
    if (version === generation) $q.notify({ type: 'negative', message: error.message })
  } finally {
    if (version === generation) titleBusy.value = false
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
      await chatCache.remove(chatId.value)
      await actions.loadChats()
      router.push('/chats')
    })
}

watch(chatId, (id) => {
  readSuspended = false
  readKey = ''
  clearManualOnOpen = true
  readError.value = ''
  controlBusy.value = false
  controlError.value = ''
  titleBusy.value = false
  approvalError.value = ''
  chat.value = null
  settingsSave = Promise.resolve(true)
  savedMessages.value = []
  activeTurn.value = null
  draft.value = ''
  attachments.value = []
  sendingDraft.value = false
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
function offline () {
  reconnecting.value = true
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
  window.addEventListener('offline', offline)
  document.addEventListener('visibilitychange', resume)
})
onUnmounted(() => {
  generation++
  stream?.close()
  off()
  offDelivery()
  window.removeEventListener('online', connectChat)
  window.removeEventListener('offline', offline)
  document.removeEventListener('visibilitychange', resume)
})
</script>

<style scoped>
.thread__queued-images { display: flex; flex-wrap: wrap; gap: 8px; }
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
  justify-content: flex-start;
  min-height: 100%;
  max-width: calc(var(--content-width) + 48px);
  margin: 0 auto;
  padding: 0 24px;
}

.thread__hint {
  padding: 40px 0;
  text-align: center;
}

.thread__queue {
  border-top: 1px solid var(--border);
  padding-top: 10px;
  margin-top: 8px;
}

.thread__queue-heading,
.thread__queued-message {
  display: flex;
  align-items: center;
  gap: 8px;
}

.thread__queued-message p {
  flex: 1;
  min-width: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 8px 0;
  font-size: 14px;
}

.thread__foot {
  /* Keep the header and every control reachable when the keyboard reduces the viewport. */
  min-height: 0;
  padding: 12px 24px 20px;
  background: var(--surface-app);
}

.thread__foot > :deep(.composer) {
  max-width: var(--content-width);
  margin: 0 auto;
}

.thread__approval {
  max-width: var(--content-width);
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
  clip-path: var(--octagon);
  color: var(--accent);
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
  color: var(--warning-text);
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
