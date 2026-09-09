<template>
  <q-menu ref="menu" anchor="top right" self="bottom right" class="package-picker" @show="load"
    @keydown.esc.stop.prevent="menu?.hide($event)">
    <div class="package-picker__head">
      <input v-model="query" class="field grow" aria-label="Search installed extensions" placeholder="Search installed extensions…" />
      <button type="button" class="btn btn--icon btn--sm" aria-label="Clear extension selection" :disabled="disabled" @click="select([])">
        <span class="material-icons" aria-hidden="true">deselect</span>
      </button>
    </div>
    <p class="caption dim package-picker__note">Select installed packages of extensions, skills and prompts. Configuration stays in Settings.</p>
    <p v-if="loading" class="caption package-picker__note" role="status">Loading extensions…</p>
    <p v-if="error" class="caption package-picker__note" role="alert">{{ error }} <button type="button" class="btn btn--sm" @click="load">Retry</button></p>
    <div class="package-picker__list scroll-y">
      <div v-for="item in filtered" :key="item.id" class="package-choice">
        <label>
          <input type="checkbox" :checked="selection.includes(item.revision)"
            :disabled="disabled || (!modelValue.includes(item.revision) && (!item.revision || modelValue.length >= 20))"
            @change="toggle(item.revision, $event.target.checked)" />
          <span class="grow"><span class="package-choice__name">{{ item.name }}</span>
            <span class="caption dim">{{ item.note }}</span></span>
        </label>
        <button v-if="item.defaultRevision && modelValue.includes(item.revision) && item.revision !== item.defaultRevision" type="button" class="btn btn--sm" :disabled="disabled" @click="useDefault(item)">Use library configuration</button>
      </div>
      <p v-if="!loading && !error && !filtered.length" class="caption dim package-picker__note">{{ query ? 'No matching installed extensions.' : 'No extensions installed. Open the library to install your first package.' }}</p>
    </div>
    <div class="package-picker__foot">
      <span class="caption dim grow">{{ modelValue.length }} selected</span>
      <RouterLink v-close-popup class="btn btn--sm" to="/settings/packages">Extension library<span class="material-icons" aria-hidden="true">arrow_outward</span></RouterLink>
    </div>
  </q-menu>
</template>
<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
const props = defineProps({ modelValue: { type: Array, default: () => [] }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const menu = ref(null), query = ref(''), installations = ref([]), revisions = ref({}), remembered = ref({}), loading = ref(false), error = ref('')
let generation = 0
const selection = ref([...props.modelValue])
watch(() => props.modelValue, value => { selection.value = [...value] })
// Native checkboxes change before the API responds. Restore the confirmed state
// when saving finishes, including failures that leave modelValue unchanged.
watch(() => props.disabled, disabled => { if (!disabled) selection.value = [...props.modelValue] })
function select (ids) {
  if (props.disabled) return
  selection.value = ids
  emit('update:modelValue', ids)
}
const choices = computed(() => {
  const selected = new Map(props.modelValue.map(id => [revisions.value[id]?.installation_id, id]))
  const rows = installations.value.filter(item => item.status === 'ready').map(item => ({
    id: item.id, revision: selected.get(item.id) || remembered.value[item.id] || item.default_revision_id, defaultRevision: item.default_revision_id,
    name: item.metadata?.resolved || item.source,
    note: selected.has(item.id) && selected.get(item.id) !== item.default_revision_id ? 'Pinned configuration · unchanged' : 'Installed'
  }))
  const known = new Set(rows.map(row => row.revision))
  for (const id of props.modelValue) if (!known.has(id)) rows.unshift({ id, revision: id, name: `Selected package · ${id.slice(0, 8)}`, note: 'Unavailable details · can be deselected' })
  return rows
})
const filtered = computed(() => choices.value.filter(item => item.name.toLowerCase().includes(query.value.trim().toLowerCase())))
async function load () {
  const current = ++generation
  loading.value = true; error.value = ''
  try {
    const data = await api.packageInstallations()
    const selected = await Promise.all(props.modelValue.map(async id => {
      try { return await api.packageRevision(id) } catch { return null }
    }))
    if (current !== generation) return
    installations.value = data.installations
    for (const revision of selected.filter(Boolean)) { revisions.value[revision.id] = revision; remembered.value[revision.installation_id] = revision.id }
  } catch (failure) { if (current === generation) error.value = failure.message }
  finally { if (current === generation) loading.value = false }
}
function useDefault (item) {
  remembered.value[item.id] = item.defaultRevision
  select(props.modelValue.map(id => id === item.revision ? item.defaultRevision : id))
}
function toggle (id, checked) {
  if (!id || props.disabled) return
  select(checked ? [...props.modelValue, id] : props.modelValue.filter(value => value !== id))
}
watch([filtered, loading, error], async () => { await nextTick(); menu.value?.updatePosition() })
onUnmounted(() => { generation++ })
</script>
<style>
.package-picker { width: min(400px, calc(100vw - 24px)); min-width: 0; }
</style>
<style scoped>
.package-picker__head, .package-picker__foot { display: flex; align-items: center; gap: 8px; padding: 8px; }
.package-picker__head { border-bottom: 1px solid var(--border); }
.package-picker__head input { min-width: 0; width: 100%; }
.package-picker__foot { border-top: 1px solid var(--border); flex-wrap: wrap; }
.package-picker__foot .material-icons { font-size: 16px; }
.package-picker__list { max-height: min(320px, 40dvh); }
.package-picker__note { margin: 0; padding: 12px; }
.package-choice { padding: 12px; }
.package-choice label { display: flex; align-items: center; gap: 10px; cursor: pointer; }
.package-choice > button { margin: 8px 0 0 26px; }
.package-choice:hover { background: var(--surface-hover); }
.package-choice input { flex: none; accent-color: var(--accent); width: 16px; height: 16px; }
.package-choice__name { display: block; overflow-wrap: anywhere; font-size: 13px; }
.package-choice .grow { min-width: 0; }
</style>
