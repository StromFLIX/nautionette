<template>
  <details class="package-config">
    <summary>Configuration & resources</summary>
    <p class="caption dim">{{ library ? 'Defaults for future selections. Existing agents and chats keep their saved configuration.' : 'Optional override. Apply changes here, then save the agent. Existing chats stay unchanged.' }}</p>
    <div class="package-config__types">
      <label v-for="kind in kinds" :key="kind"><input type="checkbox" :checked="enabled(kind)" :disabled="busy || disabled" @change="toggle(kind, $event.target.checked)" /> {{ kind }}</label>
    </div>
    <p class="caption dim">Pi themes affect terminal output, not the website. Only enable resources you trust.</p>
    <section v-for="group in groups" :key="group.key" class="package-config__group" :aria-label="group.label">
      <div class="row"><strong class="grow">{{ group.label }}</strong><button type="button" class="btn btn--sm" :disabled="busy || disabled" @click="entries[group.key].push({ key: '', value: '', saved: false })">Add {{ group.singular }}</button></div>
      <p class="caption dim">{{ group.hint }}</p>
      <div v-for="(entry, index) in entries[group.key]" :key="index" class="package-config__entry">
        <input v-model="entry.key" class="field" :aria-label="`${group.singular} ${index + 1} name`" :placeholder="group.placeholder" :disabled="busy || disabled || entry.saved" autocomplete="off" />
        <input v-if="group.key === 'env'" v-model="entry.value" class="field" type="password" :aria-label="`${group.singular} ${index + 1} value`" :placeholder="entry.saved ? 'Saved · leave blank to keep' : 'Value'" :disabled="busy || disabled" autocomplete="new-password" />
        <textarea v-else v-model="entry.value" class="field package-config__json" rows="3" :aria-label="`${group.singular} ${index + 1} content`" :placeholder="entry.saved ? 'Saved · leave blank to keep' : 'File contents'" :disabled="busy || disabled" autocomplete="off" spellcheck="false" />
        <button type="button" class="btn btn--icon btn--sm" :aria-label="`Remove ${group.singular} ${index + 1}`" :disabled="busy || disabled" @click="entries[group.key].splice(index, 1)"><span class="material-icons" aria-hidden="true">close</span></button>
      </div>
    </section>
    <p class="caption dim">Values are encrypted and never displayed again. Remove a row to delete it from the new configuration. Older snapshots retain their values.</p>
    <details class="package-config__advanced">
      <summary>Advanced resource filters</summary>
      <label :for="`filters-${revision.id}`">Resource filters (JSON)</label>
      <p class="caption dim">Omit a type to load all; [] loads none. Patterns select individual resources within the manifest.</p>
      <textarea :id="`filters-${revision.id}`" v-model="filterText" class="field package-config__json" rows="4" :disabled="busy || disabled" spellcheck="false" />
    </details>
    <p v-if="error" class="caption field-hint--bad" role="alert">{{ error }}</p>
    <button type="button" class="btn btn--sm btn--primary" :disabled="busy || disabled" @click="save">{{ busy ? 'Saving…' : library ? 'Save configuration' : 'Apply override' }}</button>
    <p v-if="saved" class="caption" role="status">{{ library ? 'Saved for future selections. Existing agents and chats are unchanged.' : 'Override applied. Save the agent to keep it.' }}</p>
  </details>
</template>
<script setup>
import { ref, watch } from 'vue'
import { api } from '../../api'
const props = defineProps({ revision: { type: Object, required: true }, disabled: Boolean, library: Boolean })
const emit = defineEmits(['replace', 'busy'])
const kinds = ['extensions', 'skills', 'prompts', 'themes']
const groups = [
  { key: 'env', label: 'Environment variables', singular: 'variable', hint: 'Use the variable names documented by the package.', placeholder: 'SERVICE_API_KEY' },
  { key: 'files', label: 'Configuration files', singular: 'file', hint: 'Files in Pi’s home: agent/name.json, .yaml, .yml, .toml or .txt. Core Pi files are reserved.', placeholder: 'agent/service.json' }
]
const filterText = ref(''), entries = ref({ env: [], files: [] }), busy = ref(false), error = ref(''), saved = ref(false)
function reset (value) {
  filterText.value = JSON.stringify(value.filters, null, 2)
  entries.value = Object.fromEntries(groups.map(group => [group.key, Object.keys(value.configuration[group.key] || {}).map(key => ({ key, value: '', saved: true }))]))
}
watch(() => props.revision, reset, { immediate: true })
function enabled (kind) { try { const value = JSON.parse(filterText.value)[kind]; return !Array.isArray(value) || value.length > 0 } catch { return false } }
function toggle (kind, enabled) {
  try {
    const filters = JSON.parse(filterText.value)
    if (enabled) delete filters[kind]; else filters[kind] = []
    filterText.value = JSON.stringify(filters, null, 2); error.value = ''
  } catch { error.value = 'Fix the resource filter JSON first.' }
}
async function save () {
  busy.value = true; emit('busy', true); error.value = ''; saved.value = false
  try {
    const configuration = {}
    for (const group of groups) {
      const values = entries.value[group.key]
      if (values.some(entry => !entry.key.trim()) || new Set(values.map(entry => entry.key.trim())).size !== values.length) throw new Error('Each entry needs a unique, non-empty name.')
      configuration[group.key] = Object.fromEntries(values.map(entry => [entry.key.trim(), entry.saved && entry.value === '' ? null : entry.value]))
    }
    const payload = { previous_id: props.revision.id, filters: JSON.parse(filterText.value), configuration }
    const revision = props.library ? await api.configurePackage(props.revision.installation_id, payload)
      : await api.createPackageRevision({ installation_id: props.revision.installation_id, ...payload })
    reset(revision) // Clear newly-entered secret values immediately.
    emit('replace', revision); saved.value = true
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false; emit('busy', false) }
}
</script>
<style scoped>
.package-config { margin-top: 12px; }
.package-config summary { cursor: pointer; font-size: 12px; }
.package-config p { margin: 12px 0; overflow-wrap: anywhere; }
.package-config label { display: block; margin: 12px 0 6px; font-size: 12px; }
.package-config__types { display: flex; flex-wrap: wrap; gap: 12px; }
.package-config__types input { accent-color: var(--accent); }
.package-config__group { margin: 20px 0; }
.package-config__group strong { font-size: 12px; }
.package-config__group .row { flex-wrap: wrap; gap: 8px; }
.package-config__entry { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr) auto; gap: 8px; margin: 10px 0; align-items: start; }
.package-config__entry .field { min-width: 0; width: 100%; }
.package-config__json { width: 100%; font-family: monospace; font-size: 12px; }
.package-config__advanced { margin: 20px 0; }
@media (max-width: 640px) {
  .package-config__entry { grid-template-columns: minmax(0, 1fr) auto; }
  .package-config__entry > :nth-child(2) { grid-column: 1; grid-row: 2; }
  .package-config__entry > button { grid-column: 2; grid-row: 1 / 3; }
}
</style>
