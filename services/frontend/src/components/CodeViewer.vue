<template>
  <div class="viewer" :class="{ 'viewer--wrap': preferences.codeWrap }">
    <nav v-if="files.length > 1" class="viewer__files">
      <button
        v-for="file in files" :key="file.name" class="viewer__file"
        :class="{ 'viewer__file--active': file.name === active }" @click="active = file.name"
      >
        <span class="material-icons">{{ file.icon || 'description' }}</span>
        <span class="truncate">{{ file.name }}</span>
      </button>
    </nav>

    <div class="viewer__body">
      <div class="viewer__bar">
        <span class="mono dim grow truncate">{{ current.name }}</span>
        <span class="caption dim">{{ lines.length }} lines</span>
        <button class="btn btn--icon btn--sm" title="Copy" @click="copy">
          <span class="material-icons" style="font-size: 0.9375rem">{{ copied ? 'check' : 'content_copy' }}</span>
        </button>
      </div>
      <div class="viewer__scroll scroll-y">
        <pre class="viewer__code"><code
          v-for="(line, index) in lines" :key="index" class="viewer__line"
        ><span class="viewer__gutter">{{ index + 1 }}</span><span
          class="viewer__text" v-html="line"
        /></code></pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { highlight } from '../highlight'
import { preferences } from '../preferences'

const props = defineProps({
  files: { type: Array, default: () => [] }
})

const active = ref(props.files[0]?.name || '')
const copied = ref(false)

const current = computed(() =>
  props.files.find((file) => file.name === active.value) || props.files[0] || { name: '', code: '' })

const lines = computed(() => highlight(current.value.code || '', current.value.language).split('\n'))

async function copy () {
  await navigator.clipboard.writeText(current.value.code || '')
  copied.value = true
  setTimeout(() => { copied.value = false }, 1200)
}

watch(() => props.files, (files) => {
  if (!files.some((file) => file.name === active.value)) active.value = files[0]?.name || ''
})
</script>

<style scoped>
.viewer {
  display: flex;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-code);
  overflow: hidden;
}

.viewer__files {
  display: flex;
  flex-direction: column;
  gap: 1px;
  flex: none;
  width: 168px;
  padding: 6px;
  border-right: 1px solid var(--border);
  background: var(--surface-panel);
}

.viewer__file {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 8px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-muted);
  font: inherit;
  font-size: 0.78125rem;
  text-align: left;
  cursor: pointer;
}

.viewer__file:hover {
  background: var(--surface-hover);
  color: var(--text);
}

.viewer__file--active {
  background: var(--accent-soft);
  color: var(--accent-hover);
}

.viewer__file .material-icons {
  font-size: 0.9375rem;
}

.viewer__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.viewer__bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 6px 5px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-panel);
  font-size: 0.75rem;
}

.viewer__scroll {
  max-height: 60vh;
  overflow-x: auto;
}

.viewer__code {
  margin: 0;
  padding: 10px 0;
  font-family: var(--font-mono);
  font-size: var(--code-font-size);
  line-height: 1.65;
}

.viewer__line {
  display: flex;
  font-size: inherit;
}

.viewer__gutter {
  flex: none;
  width: 44px;
  padding-right: 14px;
  text-align: right;
  color: var(--text-dim);
  user-select: none;
}

.viewer__text {
  white-space: pre;
  min-width: 0;
  padding-right: 16px;
}

.viewer--wrap .viewer__text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 640px) {
  .viewer { flex-direction: column; }
  .viewer__files { flex-direction: row; width: auto; overflow-x: auto; border-right: 0; border-bottom: 1px solid var(--border); }
  .viewer__file { flex: none; max-width: 240px; }
  .viewer__gutter { width: 32px; padding-right: 8px; }
}
</style>
