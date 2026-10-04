<template>
  <div class="chat-list-item" :class="{ 'chat-list-item--menu-open': menuOpen }">
    <RouterLink
      :to="`/chats/${chat.id}`"
      class="row-item" :class="{
        'row-item--active': activeRouteId === chat.id,
        'row-item--running': chat.answering && !needsInternet,
        'row-item--attention': needsInternet,
        'row-item--unread': chat.unread
      }"
    >
      <svg
        class="chat-status" :class="{ 'chat-status--active': chat.answering && !needsInternet, 'chat-status--attention': needsInternet }"
        viewBox="0 0 20 20" role="img" :aria-label="statusLabel" focusable="false"
      >
        <title>{{ statusLabel }}</title>
        <polygon points="10,2 17,6 17,14 10,18 3,14 3,6" />
      </svg>
      <span class="row-item__title grow truncate" :title="chat.title">{{ chat.title }}</span>
      <span v-if="chat.unread" class="row-item__unread" role="img" aria-label="Unread messages" title="Unread messages" />
      <span class="row-item__time" :title="startedAt ? `Started ${fullTime(startedAt)}` : undefined">{{ shortTime(startedAt) }}</span>
    </RouterLink>
    <button
      type="button" class="btn btn--icon btn--sm chat-list-item__menu"
      :aria-label="`Options for ${chat.title}`" aria-haspopup="true" :aria-expanded="menuOpen"
    >
      <span class="material-icons" aria-hidden="true">more_vert</span>
      <q-menu v-model="menuOpen" anchor="bottom right" self="top right" class="pick-menu">
        <button v-close-popup class="pick-menu__item" :disabled="readBusy === chat.id" @click="$emit('toggle-unread', chat, !chat.unread)">
          <span class="material-icons" aria-hidden="true">{{ chat.unread ? 'mark_email_read' : 'mark_email_unread' }}</span>
          {{ chat.unread ? 'Mark as read' : 'Mark as unread' }}
        </button>
      </q-menu>
    </button>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { fullTime, shortTime } from '../format'
import { chatStartedAt } from '../chat-order'

const props = defineProps({
  chat: { type: Object, required: true },
  activeRouteId: { type: String, default: '' },
  readBusy: { type: String, default: '' }
})
defineEmits(['toggle-unread'])

const menuOpen = ref(false)
const needsInternet = computed(() => ['pending', 'deciding'].includes(props.chat.internet_status))
const statusLabel = computed(() => needsInternet.value
  ? (props.chat.internet_status === 'deciding' ? 'Applying internet decision' : 'Internet approval needed')
  : props.chat.answering ? 'Active' : 'Inactive')
const startedAt = computed(() => chatStartedAt(props.chat))
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
