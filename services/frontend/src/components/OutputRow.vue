<template>
  <button type="button" class="output-row" :class="{ 'output-row--thumbnail': thumbnails }"
    :aria-label="`View output: ${output.title}`" :title="`${output.title} · Revision ${output.revision}`" @click="$emit('open', output)">
    <OutputImage v-if="thumbnails && preview" class="output-row__thumbnail" :chat-id="chatId" :output-id="output.id" :revision="output.revision" :file="preview" thumbnail />
    <span v-else class="material-icons output-row__icon" aria-hidden="true">description</span>
    <span class="output-row__details">
      <span class="output-row__title">{{ output.title }}</span>
      <span v-if="thumbnails" class="output-row__revision">Revision {{ output.revision }}</span>
    </span>
    <span v-if="!thumbnails" class="output-row__revision">rev {{ output.revision }}</span>
    <span class="material-icons output-row__arrow" aria-hidden="true">arrow_outward</span>
  </button>
</template>

<script setup>
import { computed } from 'vue'
import OutputImage from './OutputImage.vue'
import { preferences } from '../preferences'

const props = defineProps({
  chatId: { type: String, required: true },
  output: { type: Object, required: true }
})
defineEmits(['open'])
const thumbnails = computed(() => preferences.outputDisplay === 'thumbnails')
const preview = computed(() => props.output.files.find(file => file.role === 'preview' && file.previewable)
  || props.output.files.find(file => file.previewable))
</script>

<style scoped>
.output-row { display: flex; align-items: center; gap: 12px; width: 100%; max-width: 560px; min-width: 0; min-height: 44px; margin-top: 12px; padding: 8px 4px; border: 0; border-radius: var(--radius-xs); background: transparent; color: var(--text); text-align: left; font: inherit; cursor: pointer; }
.output-row + .output-row { margin-top: 0; border-top: 1px solid var(--border); }
.output-row__details { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.output-row__title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.875rem; }
.output-row__revision { flex: none; color: var(--text-muted); font-size: 0.6875rem; white-space: nowrap; }
.output-row__icon, .output-row__arrow { flex: none; color: var(--text-muted); font-size: 1.125rem; }
.output-row__arrow { font-size: 1rem; }
.output-row--thumbnail { min-height: 60px; }
.output-row__thumbnail, .output-row--thumbnail > .output-row__icon { flex: none; width: 54px; height: 44px; }
.output-row--thumbnail > .output-row__icon { display: grid; place-items: center; background: var(--surface-input); border-radius: var(--radius-xs); }
.output-row:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.output-row:active { background: var(--surface-hover); }
@media (hover: hover) and (pointer: fine) { .output-row:hover { background: var(--surface-hover); } }
</style>
