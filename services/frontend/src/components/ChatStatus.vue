<template>
  <svg class="chat-status" :class="{ 'chat-status--active': active }"
    viewBox="0 0 20 20" role="img" :aria-label="label" focusable="false">
    <title>{{ label }}</title>
    <rect x="1" y="1" width="18" height="18" rx="3" />
    <path v-if="lined" class="chat-status__lines" d="M5 6h10M5 10h10M5 14h10" />
  </svg>
</template>

<script setup>
defineProps({
  active: { type: Boolean, default: false },
  lined: { type: Boolean, default: false },
  label: { type: String, required: true }
})
</script>
