<template>
  <div class="chat-list-item">
    <RouterLink
      :to="`/chats/${chat.id}`"
      class="row-item" :class="{
        'row-item--active': activeRouteId === chat.id,
        'row-item--running': chat.answering && !needsInternet,
        'row-item--attention': needsInternet,
        'row-item--unread': chat.unread
      }"
    >
      <div class="avatar" :style="avatarStyle(chat.id)">
        {{ initials(chat.title) }}
        <svg v-if="chat.answering && !needsInternet" class="avatar__spinner" viewBox="0 0 46 46" aria-hidden="true" focusable="false">
          <circle class="avatar__spinner-track" cx="23" cy="23" r="22" />
          <circle class="avatar__spinner-arc" cx="23" cy="23" r="22" pathLength="100" />
        </svg>
      </div>
      <div class="grow">
        <div class="row">
          <span class="row-item__title grow truncate">{{ chat.title }}</span>
          <span v-if="chat.unread" class="row-item__unread" role="img" aria-label="Unread messages" title="Unread messages" />
          <span class="row-item__time">{{ shortTime(chat.updated_at) }}</span>
        </div>
        <div class="row-item__sub truncate">
          <span v-if="needsInternet" class="row-item__activity row-item__activity--attention">
            <span class="material-icons" aria-hidden="true">public</span>
            {{ chat.internet_status === 'deciding' ? 'Applying internet decision' : 'Internet approval needed' }}
          </span>
          <span v-else-if="chat.answering" class="row-item__activity">
            <span class="row-item__activity-dot" aria-hidden="true" />
            In progress
          </span>
          <template v-else>
            <span v-if="chat.last_message?.role === 'user'" class="dim">You: </span>
            {{ chat.last_message?.preview || 'No messages yet' }}
          </template>
        </div>
      </div>
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
import { avatarStyle, initials, shortTime } from '../format'

const props = defineProps({
  chat: { type: Object, required: true },
  activeRouteId: { type: String, default: '' },
  readBusy: { type: String, default: '' }
})
defineEmits(['toggle-unread'])

const needsInternet = computed(() => ['pending', 'deciding'].includes(props.chat.internet_status))
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
}

.chat-list-item__menu {
  flex: 0 0 auto;
  color: var(--text-muted);
}
</style>
