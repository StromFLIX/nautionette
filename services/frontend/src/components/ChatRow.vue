<template>
  <div class="chat-list-item">
    <RouterLink
      :to="`/chats/${chat.id}`"
      class="row-item" :class="{
        'row-item--active': activeRouteId === chat.id,
        'row-item--running': chat.answering,
        'row-item--unread': chat.unread
      }"
    >
      <svg
        class="chat-status" :class="{ 'chat-status--active': chat.answering }"
        viewBox="0 0 20 20" role="img" :aria-label="statusLabel" focusable="false"
      >
        <title>{{ statusLabel }}</title>
        <polygon points="10,2 17,6 17,14 10,18 3,14 3,6" />
      </svg>
      <span class="row-item__title grow truncate" :title="chat.title">{{ chat.title }}</span>
      <span v-if="chat.unread" class="row-item__unread" role="img" aria-label="Unread messages" title="Unread messages" />
      <span class="row-item__time" :title="startedAt ? `Started ${fullTime(startedAt)}` : undefined">{{ shortTime(startedAt) }}</span>
    </RouterLink>
    <button class="btn btn--icon btn--sm chat-list-item__menu" :aria-label="`Options for ${chat.title}`">
      <span class="material-icons" aria-hidden="true">more_vert</span>
      <q-menu anchor="bottom right" self="top right" class="pick-menu">
        <button v-close-popup class="pick-menu__item" :disabled="readBusy === chat.id" @click="$emit('toggle-unread', chat, !chat.unread)">
          <span class="material-icons" aria-hidden="true">{{ chat.unread ? 'mark_email_read' : 'mark_email_unread' }}</span>
          {{ chat.unread ? 'Mark as read' : 'Mark as unread' }}
        </button>
      </q-menu>
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fullTime, shortTime } from '../format'
import { chatStartedAt } from '../chat-order'

const props = defineProps({
  chat: { type: Object, required: true },
  activeRouteId: { type: String, default: '' },
  readBusy: { type: String, default: '' }
})
defineEmits(['toggle-unread'])

const statusLabel = computed(() => props.chat.answering ? 'Active' : 'Inactive')
const startedAt = computed(() => chatStartedAt(props.chat))
</script>

<style scoped>
/* The row must be sized by the sidebar's width, never by its own text. */
.chat-list-item {
  display: flex;
  align-items: center;
  gap: 2px;
  width: 100%;
  min-width: 0;
}

.chat-list-item > .row-item {
  flex: 1 1 auto;
  min-width: 0;
  max-width: 100%;
  min-height: 2rem;
  padding-block: calc(var(--list-padding) * 0.6);
}

.chat-list-item__menu {
  flex: 0 0 auto;
  color: var(--text-muted);
}

@media (max-width: 900px), (pointer: coarse) {
  .chat-list-item > .row-item,
  .chat-list-item__menu { min-height: 44px; }
}

@media (hover: hover) {
  .chat-list-item__menu { opacity: 0; transition: opacity var(--transition); }
  .chat-list-item:hover .chat-list-item__menu,
  .chat-list-item:focus-within .chat-list-item__menu { opacity: 1; }
}
</style>
