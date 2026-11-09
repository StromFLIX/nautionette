<template>
  <div class="chat-list-item" :class="{ 'chat-list-item--menu-open': menuOpen, 'chat-list-item--tree': descendantCount || treeDepth }"
    :style="{ '--tree-depth': Math.min(treeDepth, 4) }" :data-tree-depth="treeDepth">
    <button v-if="descendantCount" type="button" class="chat-list-item__disclosure"
      :aria-label="`${treeExpanded ? 'Collapse' : 'Expand'} agent chats for ${chat.title} (${descendantCount})`"
      :aria-expanded="treeExpanded" :title="`${descendantCount} delegated ${descendantCount === 1 ? 'chat' : 'chats'}${treeAnswering ? ' · Active' : ''}${treeUnread ? ' · Unread replies' : ''}`"
      @click="$emit('toggle-tree')">
      <span class="material-icons" aria-hidden="true">{{ treeExpanded ? 'expand_more' : 'chevron_right' }}</span>
      <span class="chat-list-item__count">{{ descendantCount }}</span>
    </button>
    <RouterLink
      :to="chatLocation(chat.id)"
      class="row-item" :class="{
        'row-item--active': activeRouteId === chat.id,
        'row-item--running': chat.answering || treeAnswering,
        'row-item--unread': chat.unread || treeUnread
      }"
    >
      <svg
        class="chat-status" :class="{ 'chat-status--active': chat.answering || treeAnswering }"
        viewBox="0 0 20 20" role="img" :aria-label="statusLabel" focusable="false"
      >
        <title>{{ statusLabel }}</title>
        <rect x="3" y="3" width="14" height="14" rx="4" />
      </svg>
      <span class="row-item__title grow truncate" :title="chat.title">{{ chat.title }}</span>
      <span v-if="chat.unread || treeUnread" class="row-item__unread" role="img" :aria-label="chat.unread ? 'Unread messages' : 'Unread agent replies'" :title="chat.unread ? 'Unread messages' : 'Unread agent replies'" />
      <span class="row-item__time" :title="activityAt ? `Last activity ${fullTime(activityAt)}` : undefined">{{ shortTime(activityAt) }}</span>
    </RouterLink>
    <button
      type="button" class="btn btn--icon btn--sm chat-list-item__menu"
      :aria-label="`Options for ${chat.title}`" aria-haspopup="true" :aria-expanded="menuOpen"
    >
      <span class="material-icons" aria-hidden="true">more_vert</span>
      <q-menu v-model="menuOpen" anchor="bottom right" self="top right" class="pick-menu">
        <ChatMenuItems :chat="chat" :unread="chat.unread" :read-busy="readBusy === chat.id"
          :title-busy="titleBusy" :has-messages="Boolean(chat.message_count)" :settings-busy="settingsBusy" :branch-busy="branchBusy" @branch="branch"
          @toggle-unread="$emit('toggle-unread', chat, $event)" @rename="rename" @regenerate-title="regenerateTitle"
          @toggle-timeout="save({ timeout_exempt: $event })" @delete="remove" />
      </q-menu>
    </button>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import { fullTime, shortTime } from '../format'
import { chatActivityAt } from '../chat-order'
import { chatLocation } from '../router'
import { api } from '../api'
import { actions } from '../store'
import { chatCache } from '../chat-cache'
import { chatDrafts } from '../chat-drafts'
import ChatMenuItems from './ChatMenuItems.vue'
import { useChatBranch } from '../chat-branch'

const props = defineProps({
  chat: { type: Object, required: true },
  activeRouteId: { type: String, default: '' },
  readBusy: { type: String, default: '' },
  treeDepth: { type: Number, default: 0 },
  descendantCount: { type: Number, default: 0 },
  treeExpanded: { type: Boolean, default: false },
  treeAnswering: { type: Boolean, default: false },
  treeUnread: { type: Boolean, default: false },
  treeActivityAt: { type: Number, default: 0 }
})
defineEmits(['toggle-unread', 'toggle-tree'])

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const menuOpen = ref(false)
const titleBusy = ref(false)
const settingsBusy = ref(false)
const { branch, branchBusy } = useChatBranch(() => props.chat)
const statusLabel = computed(() => props.chat.answering ? 'Active' : props.treeAnswering ? 'Agent chats active' : 'Inactive')
const activityAt = computed(() => props.treeActivityAt || chatActivityAt(props.chat))

// List actions must not open the conversation or act on whichever chat is open.
async function save (fields) {
  const id = props.chat.id
  if (settingsBusy.value) return
  settingsBusy.value = true
  try {
    await api.updateChat(id, fields)
    await actions.loadChats()
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    settingsBusy.value = false
  }
}

function rename () {
  const { id, title } = props.chat
  $q.dialog({
    title: 'Rename chat',
    prompt: { model: title, type: 'text', outlined: true },
    cancel: true
  }).onOk(async (title) => {
    try {
      await api.updateChat(id, { title })
      await actions.loadChats()
    } catch (error) {
      $q.notify({ type: 'negative', message: error.message })
    }
  })
}

async function regenerateTitle () {
  if (titleBusy.value) return
  const id = props.chat.id
  titleBusy.value = true
  try {
    await api.regenerateChatTitle(id)
    await actions.loadChats()
    $q.notify({ type: 'positive', message: 'Chat title regenerated' })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    titleBusy.value = false
  }
}

function remove () {
  const { id, title } = props.chat
  $q.dialog({ title: 'Delete chat', message: `Delete “${title}”?`, cancel: true })
    .onOk(async () => {
      try {
        await api.deleteChat(id)
        // Don't navigate away from another chat, including one opened while the
        // confirmation or request was pending.
        if (route.name === 'chats' && route.params.id === id) await router.push(chatLocation())
        await chatCache.remove(id)
        await chatDrafts.remove(id)
        await actions.loadChats()
      } catch (error) {
        $q.notify({ type: 'negative', message: error.message })
      }
    })
}
</script>

<style scoped>
/* The row must be sized by the sidebar's width, never by its own text. */
.chat-list-item {
  --chat-menu-width: 2rem;
  position: relative;
  width: 100%;
  min-width: 0;
}

.chat-list-item > .row-item {
  min-width: 0;
  max-width: 100%;
  min-height: 2rem;
  padding-block: calc(var(--list-padding) * 0.6);
  padding-inline-end: calc(var(--chat-menu-width) + 8px);
}

.chat-list-item--tree {
  padding-inline-start: calc(var(--tree-depth) * 10px + 36px);
}

.chat-list-item__disclosure {
  position: absolute;
  inset-inline-start: calc(var(--tree-depth) * 10px);
  top: 0;
  height: 100%;
  width: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 1px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: 6px;
}
.chat-list-item__disclosure:hover { color: var(--text); }
.chat-list-item__disclosure:focus-visible { outline: 2px solid currentColor; outline-offset: -2px; }
.chat-list-item__disclosure .material-icons { font-size: 16px; }
.chat-list-item__count { font-size: 0.65rem; font-variant-numeric: tabular-nums; }

.chat-list-item__menu {
  position: absolute;
  inset-inline-end: 4px;
  top: 50%;
  transform: translateY(-50%);
  width: var(--chat-menu-width);
  color: var(--text-muted);
}

@media (max-width: 900px), (pointer: coarse) {
  .chat-list-item { --chat-menu-width: 44px; }
  .chat-list-item > .row-item,
  .chat-list-item__menu { min-height: 44px; }
}

/* Touch keeps an always-visible action. Mouse/keyboard users get the full row
   until they interact; only the available text width changes, not its size. */
@media (hover: hover) and (pointer: fine) {
  .chat-list-item > .row-item { padding-inline-end: 10px; }
  .chat-list-item__menu {
    opacity: 0;
    pointer-events: none;
  }
  .chat-list-item:is(:hover, :focus-within, .chat-list-item--menu-open) > .row-item {
    padding-inline-end: calc(var(--chat-menu-width) + 8px);
  }
  .chat-list-item:is(:hover, :focus-within, .chat-list-item--menu-open) .chat-list-item__menu {
    opacity: 1;
    pointer-events: auto;
  }
}
</style>
