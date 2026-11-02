<template>
  <span class="app-avatar" :style="avatarStyle(app.id || app.title || 'app')" aria-hidden="true">
    <img v-if="image && !failed" :src="image" alt="" @error="failed = true" />
    <span v-else class="material-icons">{{ icon }}</span>
  </span>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { avatarStyle } from '../format'
const props = defineProps({ app: { type: Object, required: true } })
const failed = ref(false)
// Defense in depth for old/imported metadata: never load remote assets or SVG.
const image = computed(() => /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/.test(props.app.image || '') ? props.app.image : '')
const icon = computed(() => /^[a-z][a-z0-9_]{0,47}$/.test(props.app.icon || '') ? props.app.icon : 'apps')
watch(image, () => { failed.value = false })
</script>

<style scoped>
.app-avatar { display: inline-flex; align-items: center; justify-content: center; flex: none; width: var(--app-avatar-size, 36px); height: var(--app-avatar-size, 36px); border-radius: var(--radius-sm); overflow: hidden; }
.app-avatar img { width: 100%; height: 100%; object-fit: cover; }
.app-avatar .material-icons { font-size: calc(var(--app-avatar-size, 36px) * .55); }
</style>
