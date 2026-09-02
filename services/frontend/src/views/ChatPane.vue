<template>
  <ChatWelcome v-if="!chatId" :busy="starting" @start="start" />

  <div v-else class="thread stack grow">
    <header class="pane-head">
      <button class="btn btn--icon pane-head__back" @click="$router.push('/chats')">
        <span class="material-icons">arrow_back</span>
      </button>
      <div class="avatar-sm" :style="avatarStyle(chatId)">{{ initials(chat?.title || '?') }}</div>
      <div class="grow">
        <div class="pane-head__title truncate">{{ chat?.title || 'Chat' }}</div>
        <div class="caption dim truncate">
          {{ messages.length }} messages · {{ chat?.agent_set }}
          <template v-if="chat?.promoted_to"> · → {{ chat.promoted_to }}</template>
        </div>
      </div>
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
        />
        <MessageBubble
          v-if="streaming" role="assistant" live
          :content="liveSteps.length || liveStatus ? '' : '…'" :meta="{ steps: liveSteps }"
          :status="liveStatus"
        />
      </div>
    </div>

    <div class="thread__foot">
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
import { useRoute, useRouter } from 'vue-router'
import ChatWelcome from '../components/ChatWelcome.vue'
import Composer from '../components/Composer.vue'
import MessageBubble from '../components/MessageBubble.vue'
import { avatarStyle, initials } from '../format'
import { foldEvent } from '../timeline'
import { actions, onLiveEvent, store } from '../store'
import { api, streamMessage } from '../api'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()

const chat = ref(null)
const messages = ref([])
const draft = ref('')
const streaming = ref(false)
const liveSteps = ref([])
const liveStatus = ref('')
const starting = ref(false)
const scroller = ref(null)
const composer = ref(null)

const chatId = computed(() => route.params.id || '')
const contextUsed = computed(() =>
  messages.value.reduce((total, message) => total + (message.content || '').length, 0))

async function load (id) {
  const data = await api.chat(id)
  chat.value = data.chat
  messages.value = data.messages
  scrollDown('auto')
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
    await load(created.id)
    draft.value = text
    await send()
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    starting.value = false
  }
}

async function send () {
  const text = draft.value.trim()
  if (!text || streaming.value) return
  draft.value = ''
  streaming.value = true
  liveSteps.value = []
  liveStatus.value = ''
  try {
    await streamMessage(chatId.value, text, (event) => {
      if (event.type === 'user_message') messages.value.push(event.message)
      // The broker narrates a cold start (building the agent image) before it runs.
      else if (event.type === 'status') liveStatus.value = event.message || ''
      else if (event.type === 'error') $q.notify({ type: 'negative', message: event.message })
      else if (event.type === 'done') messages.value.push(event.message)
      else {
        liveStatus.value = ''
        foldEvent(liveSteps.value, event)
      }
      scrollDown()
    })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    streaming.value = false
    liveSteps.value = []
    liveStatus.value = ''
    actions.loadChats()
    actions.loadWorkflows()
    nextTick(() => composer.value?.focus())
  }
}

async function patch (fields) {
  chat.value = await api.updateChat(chatId.value, fields)
  actions.loadChats()
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
  if (id) load(id)
  else {
    chat.value = null
    messages.value = []
  }
})

let off = () => {}
onMounted(() => {
  if (chatId.value) load(chatId.value)
  // A workflow run can post into the chat that is open right now.
  off = onLiveEvent((event) => {
    if (event.kind === 'chat.answered' && event.chat_id === chatId.value && !streaming.value) {
      load(chatId.value)
    }
  })
})
onUnmounted(() => off())
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
