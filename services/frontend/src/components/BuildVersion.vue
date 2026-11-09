<template>
  <a v-if="info.commit" class="build-version" :href="`${brand.repository}/commit/${info.commit}`"
    target="_blank" rel="noopener noreferrer" :aria-label="`View commit ${info.commit}`" :title="`Commit ${info.commit}`">{{ info.label }}</a>
  <span v-else class="build-version">{{ info.label }}</span>
</template>

<script setup>
import { computed } from 'vue'
import { brand } from '../brand'
import { formatBuildInfo } from '../build-info'

const props = defineProps({ system: { type: Object, default: () => ({}) } })
const info = computed(() => formatBuildInfo(props.system))
</script>

<style scoped>
.build-version { white-space: nowrap; }
a.build-version { color: inherit; text-decoration: underline; text-underline-offset: .2em; }
</style>
