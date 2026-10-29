<template>
  <span class="agent-avatar" :style="avatarStyle(agent?.id || 'all')" aria-hidden="true">
    <img v-if="agent?.image && !failed" :src="agent.image" alt="" @error="failed = true" />
    <span v-else-if="agent" class="agent-avatar__initials">{{ initials(agent.name) }}</span>
    <span v-else class="material-icons">forum</span>
  </span>
</template>

<script setup>
import { ref, watch } from 'vue'
import { avatarStyle, initials } from '../format'
const props = defineProps({ agent: { type: Object, default: null } })
const failed = ref(false)
watch(() => props.agent?.image, () => { failed.value = false })
</script>

<style scoped>
.agent-avatar { display: inline-flex; align-items: center; justify-content: center; flex: none; width: var(--agent-avatar-size, 36px); height: var(--agent-avatar-size, 36px); overflow: hidden; clip-path: var(--octagon); font-weight: 650; }
.agent-avatar img { width: 100%; height: 100%; object-fit: cover; }
.agent-avatar__initials { font-size: 0.8125rem; }
.agent-avatar .material-icons { font-size: 1.25rem; }
</style>
