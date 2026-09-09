<template>
  <details class="package-config">
    <summary>Configuration & resources</summary>
    <p class="caption dim">Changes create a new revision. Save the agent afterwards; existing chats and accepted turns stay unchanged.</p>
    <div class="row package-config__types">
      <label v-for="kind in kinds" :key="kind"><input type="checkbox" :checked="enabled(kind)" :disabled="busy || disabled" @change="toggle(kind, $event.target.checked)" /> {{ kind }}</label>
    </div>
    <label :for="`filters-${revision.id}`">Resource filters (JSON)</label>
    <p class="caption dim">Omit a type to load all; [] loads none. Patterns select individual resources within the manifest.</p>
    <textarea :id="`filters-${revision.id}`" v-model="filterText" class="field package-config__json" rows="4" :disabled="busy || disabled" spellcheck="false" />
    <label :for="`config-${revision.id}`">Managed configuration (JSON, write-only values)</label>
    <p class="caption dim">Use env for environment variables and files for agent/*.json, *.yaml, *.yml, *.toml or *.txt in Pi's home. Core Pi settings are reserved. null keeps a saved value; omit a key to remove it. All values must be strings. Never enter credentials in chat.</p>
    <textarea :id="`config-${revision.id}`" v-model="configText" class="field package-config__json" rows="7" :disabled="busy || disabled" autocomplete="off" spellcheck="false" />
    <p class="caption dim">Example: {"env":{"SERVICE_API_KEY":"…"},"files":{"agent/web-search.json":"{\"provider\":\"example\"}"}}</p>
    <p v-if="error" class="caption field-hint--bad" role="alert">{{ error }}</p>
    <button type="button" class="btn btn--sm" :disabled="busy || disabled" @click="save">{{ busy ? 'Saving…' : 'Use configuration revision' }}</button>
  </details>
</template>
<script setup>
import { ref, watch } from 'vue'
import { api } from '../../api'
const props = defineProps({ revision: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['replace', 'busy'])
const kinds = ['extensions', 'skills', 'prompts', 'themes']
const filterText = ref(''), configText = ref(''), busy = ref(false), error = ref('')
watch(() => props.revision, value => {
  filterText.value = JSON.stringify(value.filters, null, 2)
  configText.value = JSON.stringify(value.configuration, null, 2)
}, { immediate: true })
function enabled (kind) { try { const value = JSON.parse(filterText.value)[kind]; return !Array.isArray(value) || value.length > 0 } catch { return false } }
function toggle (kind, enabled) {
  try {
    const filters = JSON.parse(filterText.value)
    if (enabled) delete filters[kind]; else filters[kind] = []
    filterText.value = JSON.stringify(filters, null, 2); error.value = ''
  } catch { error.value = 'Fix the resource filter JSON first.' }
}
async function save () {
  busy.value = true; emit('busy', true); error.value = ''
  try {
    const revision = await api.createPackageRevision({ installation_id: props.revision.installation_id,
      previous_id: props.revision.id, filters: JSON.parse(filterText.value), configuration: JSON.parse(configText.value) })
    configText.value = JSON.stringify(revision.configuration, null, 2) // Clear newly-entered secret values.
    emit('replace', revision.id)
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false; emit('busy', false) }
}
</script>
<style scoped>
.package-config { margin-top: 12px; }
.package-config summary { cursor: pointer; font-size: 12px; }
.package-config label { display: block; margin: 14px 0 6px; font-size: 12px; }
.package-config__types { flex-wrap: wrap; gap: 12px; }
.package-config__json { width: 100%; font-family: monospace; font-size: 12px; }
.package-config p { overflow-wrap: anywhere; }
</style>
