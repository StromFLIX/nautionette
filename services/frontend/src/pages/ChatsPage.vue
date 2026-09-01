<template>
  <q-page class="row no-wrap" style="height: calc(100vh - 50px)">
    <div class="col-3 gt-sm column" style="min-width: 240px; border-right: 1px solid var(--line)">
      <div class="q-pa-sm">
        <q-btn class="full-width" color="primary" icon="add" label="New chat" unelevated @click="newChat" />
      </div>
      <q-scroll-area class="col">
        <q-list separator>
          <q-item
            v-for="chat in chats" :key="chat.id" clickable v-ripple
            :active="chat.id === activeId" active-class="bg-blue-grey-10"
            @click="open(chat.id)"
          >
            <q-item-section>
              <q-item-label lines="1">{{ chat.title }}</q-item-label>
              <q-item-label caption>
                {{ chat.message_count }} messages
                <span v-if="chat.promoted_to"> · → {{ chat.promoted_to }}</span>
              </q-item-label>
            </q-item-section>
            <q-item-section side>
              <q-btn dense flat round size="sm" icon="delete" @click.stop="remove(chat.id)" />
            </q-item-section>
          </q-item>
        </q-list>
      </q-scroll-area>
    </div>

    <div class="col column">
      <div v-if="!activeId" class="col column flex-center text-grey-6 q-pa-xl text-center">
        <q-icon name="forum" size="64px" class="q-mb-md" />
        <div class="text-h6">Start a chat</div>
        <div class="q-mt-sm" style="max-width: 460px">
          Talk to the system. When a conversation is something you want again, promote it
          and it becomes a durable workflow you can schedule.
        </div>
        <q-btn class="q-mt-lg" color="primary" label="New chat" unelevated @click="newChat" />
      </div>

      <template v-else>
        <div class="row items-center q-pa-sm q-gutter-sm" style="border-bottom: 1px solid var(--line)">
          <div class="text-subtitle1 ellipsis col">{{ chat?.title }}</div>
          <q-btn
            dense outline color="primary" icon="auto_awesome" label="Promote to workflow"
            :loading="promoting" :disable="!messages.length" @click="promote"
          />
        </div>

        <q-scroll-area ref="scroller" class="col q-pa-md">
          <div v-for="message in messages" :key="message.id" class="q-mb-md row">
            <div :class="['bubble', message.role === 'user' ? 'bubble-user' : 'bubble-agent']">
              {{ message.content }}
              <div v-if="message.meta?.tools?.length" class="text-caption text-grey-5 q-mt-xs">
                tools: {{ message.meta.tools.join(', ') }}
              </div>
            </div>
          </div>
          <div v-if="streaming" class="q-mb-md row">
            <div class="bubble bubble-agent">
              {{ streamed || '…' }}
              <div v-if="activeTool" class="text-caption text-grey-5 q-mt-xs">running {{ activeTool }}</div>
            </div>
          </div>
        </q-scroll-area>

        <div class="q-pa-sm" style="border-top: 1px solid var(--line)">
          <q-input
            v-model="draft" outlined dense autogrow type="textarea" :disable="streaming"
            placeholder="Ask for something. Say 'every day at 8' when you want it to repeat."
            @keydown.enter.exact.prevent="send"
          >
            <template #after>
              <q-btn round dense color="primary" icon="send" :loading="streaming" @click="send" />
            </template>
          </q-input>
        </div>
      </template>
    </div>
  </q-page>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import { api, streamMessage } from '../api'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()

const chats = ref([])
const chat = ref(null)
const messages = ref([])
const activeId = ref(route.params.id || '')
const draft = ref('')
const streaming = ref(false)
const streamed = ref('')
const activeTool = ref('')
const promoting = ref(false)
const scroller = ref(null)

async function loadChats () {
  chats.value = (await api.chats()).chats
}

async function open (id) {
  activeId.value = id
  router.replace(`/chats/${id}`)
  const data = await api.chat(id)
  chat.value = data.chat
  messages.value = data.messages
  scrollDown()
}

async function newChat () {
  const created = await api.createChat('New chat')
  await loadChats()
  open(created.id)
}

async function remove (id) {
  await api.deleteChat(id)
  if (id === activeId.value) {
    activeId.value = ''
    chat.value = null
    messages.value = []
  }
  loadChats()
}

function scrollDown () {
  nextTick(() => scroller.value?.setScrollPercentage('vertical', 1, 120))
}

async function send () {
  const text = draft.value.trim()
  if (!text || streaming.value) return
  draft.value = ''
  streaming.value = true
  streamed.value = ''
  activeTool.value = ''
  try {
    await streamMessage(activeId.value, text, (event) => {
      if (event.type === 'user_message') messages.value.push(event.message)
      else if (event.type === 'delta') streamed.value += event.text
      else if (event.type === 'tool') activeTool.value = event.name
      else if (event.type === 'error') $q.notify({ type: 'negative', message: event.message })
      else if (event.type === 'done') {
        messages.value.push(event.message)
        streamed.value = ''
      }
      scrollDown()
    })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    streaming.value = false
    activeTool.value = ''
    loadChats()
  }
}

async function promote () {
  promoting.value = true
  try {
    const result = await api.promote(activeId.value)
    $q.notify({
      type: result.validation?.valid ? 'positive' : 'warning',
      message: result.validation?.valid
        ? `Draft ${result.name} is ready for review`
        : `Draft ${result.name} needs work: ${(result.validation?.errors || []).join('; ')}`,
      timeout: 6000
    })
    router.push(`/workflows/${result.name}`)
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    promoting.value = false
    loadChats()
  }
}

watch(() => route.params.id, (id) => { if (id && id !== activeId.value) open(id) })

onMounted(async () => {
  await loadChats()
  if (activeId.value) open(activeId.value)
})
</script>
