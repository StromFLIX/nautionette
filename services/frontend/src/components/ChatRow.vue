<template>
  <div class="chat-list-item" :class="{ 'chat-list-item--menu-open': menuOpen, 'chat-list-item--unread': unread }" :data-tree-depth="treeDepth">
    <RouterLink
      :to="chatLocation(chat.id)" :aria-describedby="statusId"
      class="row-item" :class="{
        'row-item--active': activeRouteId === chat.id,
        'row-item--running': active,
        'row-item--unread': unread
      }"
    >
      <span class="row-item__title truncate" :title="chat.title">{{ chat.title }}</span>
    </RouterLink>
    <div class="chat-list-item__meta">
      <div class="chat-list-item__status">
        <button v-if="treeCount" type="button" class="chat-list-item__tree"
          :aria-label="`${treeOpen ? 'Collapse' : 'Expand'} agent chats for ${chat.title} (${treeCount})`"
          :aria-expanded="treeOpen" :aria-controls="treeOpen ? treeChildrenId : undefined"
          :aria-describedby="statusId" :title="`${treeCount} agent ${treeCount === 1 ? 'chat' : 'chats'} · ${statusLabel}`"
          @click="$emit('toggle-tree', chat.id)">
          <ChatStatus :id="statusId" :active="active" :unread="unread" lined :label="statusLabel" />
          <span class="chat-list-item__count" aria-hidden="true">{{ treeCount > 99 ? '99+' : treeCount }}</span>
        </button>
        <ChatStatus v-else :id="statusId" :active="active" :unread="unread" :label="statusLabel" />
      </div>
      <div class="chat-list-item__trailing">
        <span class="row-item__time" :title="activityAt ? `Last activity ${fullTime(activityAt)}` : undefined">{{ shortTime(activityAt) }}</span>
        <button
          type="button" class="btn btn--icon btn--sm chat-list-item__menu"
          :aria-label="`Options for ${chat.title}`" aria-haspopup="true" :aria-expanded="menuOpen"
          :title="activityAt ? `Last activity ${fullTime(activityAt)}` : undefined"
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
    </div>
  </div>
</template>

<script setup>
import { computed, ref, useId } from 'vue'
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
import ChatStatus from './ChatStatus.vue'
import { useChatBranch } from '../chat-branch'

const props = defineProps({
  chat: { type: Object, required: true },
  activeRouteId: { type: String, default: '' },
  readBusy: { type: String, default: '' },
  treeDepth: { type: Number, default: 0 },
  treeAnswering: { type: Boolean, default: false },
  treeUnread: { type: Boolean, default: false },
  treeActivityAt: { type: Number, default: 0 },
  treeCount: { type: Number, default: 0 },
  treeOpen: { type: Boolean, default: false },
  treeChildrenId: { type: String, default: '' }
})
defineEmits(['toggle-unread', 'toggle-tree'])

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const menuOpen = ref(false)
const titleBusy = ref(false)
const settingsBusy = ref(false)
const { branch, branchBusy } = useChatBranch(() => props.chat)
const statusId = useId()
const active = computed(() => props.chat.answering || props.treeAnswering)
const unread = computed(() => props.chat.unread || props.treeUnread)
const statusLabel = computed(() => [
  props.chat.answering ? 'Active' : props.treeAnswering ? 'Agent chats active' : 'Inactive',
  props.chat.unread ? 'Unread messages' : props.treeUnread ? 'Unread agent replies' : ''
].filter(Boolean).join(' · '))
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
/* Overlay sibling controls on a full-row link without nesting buttons inside it.
   Fixed metadata slots align tiles across rows, regardless of count or timestamp. */
.chat-list-item {
  --chat-menu-width: 2rem;
  --chat-status-width: 1.75rem;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  position: relative;
  width: 100%;
  min-width: 0;
}

.chat-list-item > .row-item {
  grid-area: 1 / 1 / 2 / 3;
  display: grid;
  grid-template-columns: subgrid;
  gap: 0;
  min-width: 0;
  max-width: 100%;
  min-height: 2rem;
  padding-block: calc(var(--list-padding) * 0.6);
}
.chat-list-item:hover > .row-item { background: var(--surface-hover); }
.row-item__title { margin-inline-end: 6px; }

.chat-list-item__meta {
  grid-area: 1 / 2;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 4px;
  margin-inline-end: 4px;
  pointer-events: none;
}

.chat-list-item__status {
  display: grid;
  place-items: center;
  flex: none;
  width: var(--chat-status-width);
}
.chat-list-item__tree {
  position: relative;
  display: grid;
  place-items: center;
  width: 100%;
  min-height: 2rem;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-dim);
  font: inherit;
  cursor: pointer;
  pointer-events: auto;
}
.chat-list-item__tree:hover { background: var(--accent-soft); color: var(--text); }
.chat-list-item__tree:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
/* A flat corner badge is outside the tile's pulse and never changes its width.
   Large families show 99+; accessible names and tooltips retain the exact count. */
.chat-list-item__count {
  position: absolute;
  inset-inline-end: 0;
  inset-block-start: calc(50% - .9rem);
  min-width: .75rem;
  max-width: 100%;
  box-sizing: border-box;
  padding-inline: 1px;
  border-radius: .2rem;
  background: var(--surface-panel);
  color: var(--text-muted);
  font-size: .5625rem;
  line-height: 1.25;
  text-align: center;
  font-variant-numeric: tabular-nums;
  pointer-events: none;
}

/* Time and options share one stable slot; revealing options never moves text. */
.chat-list-item__trailing {
  display: grid;
  align-items: center;
  justify-items: center;
  flex: none;
  width: max(var(--chat-menu-width), 7.5ch);
  min-width: 0;
  font-size: .6875rem;
}
.chat-list-item__trailing > * { grid-area: 1 / 1; }
.row-item__time {
  width: 100%;
  min-width: 0;
  padding-inline: 0;
  box-sizing: border-box;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: end;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.chat-list-item__menu {
  width: var(--chat-menu-width);
  color: var(--text-muted);
  justify-self: end;
  pointer-events: auto;
}

/* Touch retains a 44px target and visible options; last activity is available
   in the options tooltip rather than occupying another column. */
@media (max-width: 900px), (pointer: coarse) {
  .chat-list-item {
    --chat-menu-width: 44px;
    --chat-status-width: max(1.75rem, 44px);
  }
  .chat-list-item > .row-item,
  .chat-list-item__menu { min-height: 44px; }
  .chat-list-item__tree { min-width: 44px; min-height: 44px; }
}
.row-item__time { visibility: hidden; }

@media (hover: hover) and (pointer: fine) {
  .row-item__time { visibility: visible; }
  .chat-list-item__menu { opacity: 0; pointer-events: none; }
  .chat-list-item:is(:hover, :focus-within, .chat-list-item--menu-open) .row-item__time { visibility: hidden; }
  .chat-list-item:is(:hover, :focus-within, .chat-list-item--menu-open) .chat-list-item__menu {
    opacity: 1;
    pointer-events: auto;
  }
}
</style>
