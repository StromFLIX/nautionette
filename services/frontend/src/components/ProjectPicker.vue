<template>
  <q-menu ref="menu" anchor="top right" self="bottom right" class="project-picker" @show="load"
    @keydown.esc.stop.prevent="menu?.hide($event)">
    <div class="project-picker__head">
      <input v-model="query" class="field grow" aria-label="Search projects" placeholder="Search projects" />
      <button class="btn btn--icon btn--sm" aria-label="Clear project selection" @click="$emit('update:modelValue', [])">
        <span class="material-icons">deselect</span><q-tooltip>Clear selection</q-tooltip>
      </button>
    </div>
    <p v-if="loading" class="caption dim project-picker__note" role="status">Loading projects...</p>
    <div v-else-if="error" class="project-picker__note" role="alert">
      <p class="caption">{{ error }}</p>
      <button class="btn btn--sm" @click="load">Retry</button>
    </div>
    <div v-else class="project-picker__list scroll-y">
      <label v-for="project in filtered" :key="project.id" class="project-choice">
        <input type="checkbox" :checked="modelValue.includes(project.id)"
          :disabled="!modelValue.includes(project.id) && (project.status !== 'ready' || modelValue.length >= 20)"
          @change="toggle(project.id, $event.target.checked)" />
        <span class="grow">
          <span class="project-choice__name">{{ project.full_name }}</span>
          <span v-if="project.status !== 'ready'" class="caption dim">{{ project.status }}</span>
        </span>
      </label>
      <p v-if="!filtered.length" class="caption dim project-picker__note">{{ query ? 'No matching projects.' : 'No projects added.' }}</p>
    </div>
    <div class="project-picker__foot">
      <span class="caption dim grow">{{ modelValue.length }} selected</span>
      <button class="btn btn--sm" @click="actions.openSettings('projects')">
        <span class="material-icons">folder_open</span>Manage projects
      </button>
    </div>
  </q-menu>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { api } from '../api'
import { actions } from '../store'

const props = defineProps({ modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
const menu = ref(null)
const projects = ref([])
const query = ref('')
const loading = ref(false)
const error = ref('')
const filtered = computed(() => {
  const available = new Set(projects.value.map((project) => project.id))
  const missing = props.modelValue.filter((id) => !available.has(id))
    .map((id) => ({ id, full_name: id, status: 'unavailable' }))
  return [...projects.value, ...missing].filter((project) =>
    project.full_name.toLowerCase().includes(query.value.trim().toLowerCase()))
})

async function load () {
  loading.value = true
  error.value = ''
  try { projects.value = (await api.projects()).projects } catch (failure) { error.value = failure.message }
  finally { loading.value = false }
}

function toggle (id, checked) {
  emit('update:modelValue', checked ? [...props.modelValue, id] : props.modelValue.filter((selected) => selected !== id))
}

watch([filtered, loading, error], async () => {
  await nextTick()
  menu.value?.updatePosition()
})
</script>

<style>
.project-picker { width: min(380px, calc(100vw - 24px)); min-width: 0; }
</style>
<style scoped>
.project-picker__head, .project-picker__foot { display: flex; align-items: center; gap: 8px; padding: 8px; }
.project-picker__head { border-bottom: 1px solid var(--border); }
.project-picker__head input { min-width: 0; width: 100%; }
.project-picker__foot { border-top: 1px solid var(--border); }
.project-picker__foot .caption, .project-picker__foot .btn { white-space: nowrap; }
.project-picker__foot .material-icons { font-size: 16px; }
.project-picker__list { max-height: min(320px, 40dvh); }
.project-picker__note { margin: 0; padding: 12px; }
.project-choice { display: flex; align-items: center; gap: 10px; padding: 10px; cursor: pointer; }
.project-choice:hover { background: var(--surface-hover); }
.project-choice input { flex: none; accent-color: var(--accent); width: 16px; height: 16px; }
.project-choice__name { display: block; overflow-wrap: anywhere; font-size: 13px; }
.project-choice .grow { min-width: 0; }
</style>