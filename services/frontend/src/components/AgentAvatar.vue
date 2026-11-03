<template>
  <span class="agent-avatar" :style="avatarStyle(agent?.id || 'all')" aria-hidden="true">
    <span v-if="agent" v-show="!loaded" class="agent-avatar__initials">{{ initials(agent.name) }}</span>
    <span v-else class="material-icons">{{ icon }}</span>
    <img v-if="agent?.image && !failed" :key="agent.image" :src="agent.image" :class="{ 'agent-avatar__image--pending': !loaded }" alt="" width="40" height="40" loading="lazy" decoding="async" @load="loaded = true" @error="failed = true; loaded = false" />
  </span>
</template>

<script setup>
import { ref, watch } from 'vue'
import { avatarStyle, initials } from '../format'
const props = defineProps({ agent: { type: Object, default: null }, icon: { type: String, default: 'forum' } })
const failed = ref(false), loaded = ref(false)
watch(() => props.agent?.image, () => { failed.value = false; loaded.value = false })
</script>

<style scoped>
.agent-avatar { position: relative; display: inline-flex; align-items: center; justify-content: center; flex: none; width: var(--agent-avatar-size, 36px); height: var(--agent-avatar-size, 36px); overflow: hidden; border-radius: var(--radius-sm); font-weight: 650; }
.agent-avatar img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.agent-avatar__image--pending { opacity: 0; }
.agent-avatar__initials { font-size: 0.8125rem; }
.agent-avatar .material-icons { font-size: 1.25rem; }
</style>
