<template>
  <button v-close-popup class="pick-menu__item" :disabled="readBusy" @click="$emit('toggle-unread', !unread)">
    <span class="material-icons" aria-hidden="true">{{ unread ? 'mark_email_read' : 'mark_email_unread' }}</span>
    {{ unread ? 'Mark as read' : 'Mark as unread' }}
  </button>
  <button v-close-popup class="pick-menu__item" @click="$emit('rename')">
    <span class="material-icons pick__icon" aria-hidden="true">edit</span>Rename
  </button>
  <button v-close-popup class="pick-menu__item" :disabled="titleBusy || !hasMessages" @click="$emit('regenerate-title')">
    <span class="material-icons pick__icon" aria-hidden="true">autorenew</span>{{ titleBusy ? 'Regenerating title…' : 'Regenerate title' }}
  </button>
  <button v-close-popup class="pick-menu__item" role="menuitemcheckbox" :aria-checked="Boolean(chat?.timeout_exempt)"
    :disabled="!chat || settingsBusy" @click="$emit('toggle-timeout', !chat?.timeout_exempt)">
    <span class="material-icons pick__icon" aria-hidden="true">{{ chat?.timeout_exempt ? 'timer_off' : 'timer' }}</span>
    <span class="stack grow">
      <span>Disable agent timeout</span>
      <span class="caption dim">New messages only; running and queued unchanged</span>
    </span>
    <span v-if="chat?.timeout_exempt" class="material-icons pick-menu__check" aria-hidden="true">check</span>
    <q-tooltip>Applies to newly sent messages in this chat, not running or queued messages. Stop and tool-specific limits still apply; usage can continue until stopped.</q-tooltip>
  </button>
  <button v-close-popup class="pick-menu__item" @click="$emit('delete')">
    <span class="material-icons pick__icon" aria-hidden="true" style="color: var(--danger)">delete</span>Delete chat
  </button>
</template>

<script setup>
defineProps({
  chat: { type: Object, default: null },
  unread: { type: Boolean, default: false },
  readBusy: { type: Boolean, default: false },
  titleBusy: { type: Boolean, default: false },
  hasMessages: { type: Boolean, default: false },
  settingsBusy: { type: Boolean, default: false }
})
defineEmits(['toggle-unread', 'rename', 'regenerate-title', 'toggle-timeout', 'delete'])
</script>
