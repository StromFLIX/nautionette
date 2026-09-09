<template>
  <section class="agent-packages" aria-label="Agent Pi packages">
    <h3>Pi packages</h3>
    <p class="caption dim">Extensions, skills and prompts for this agent. Package code is shared read-only; configuration is pinned per agent/chat. Third-party packages run with the agent's access, including its projects and supplied secrets. Only install code you trust.</p>
    <p class="caption dim">Installation does not guarantee compatibility. Terminal widgets, interactive setup/OAuth, extra OS dependencies and cross-turn extension state are not supported yet. Settings is the only supported configuration surface.</p>
    <p v-if="error" role="alert" class="caption field-hint--bad">{{ error }} <button type="button" class="btn btn--sm" @click="load">Retry loading</button></p>
    <article v-for="id in modelValue" :key="id" class="agent-packages__card">
      <div class="row"><strong class="grow">{{ title(revisions[id]?.installation_id) }}</strong>
        <button type="button" class="btn btn--sm" :disabled="disabled || busy" @click="remove(id)">Remove from agent</button></div>
      <span class="caption dim">Revision {{ id.slice(0, 8) }} · installed, compatibility unverified</span>
      <PackageConfiguration v-if="revisions[id]" :revision="revisions[id]" :disabled="disabled || busy" @replace="replace(id, $event)" @busy="configurationBusy = $event" />
      <p v-else class="caption dim">Loading configuration…</p>
    </article>
    <label class="agent-packages__label" for="package-source">Install from npm or public GitHub</label>
    <div class="row agent-packages__source"><input id="package-source" v-model="source" class="field grow" placeholder="npm:package@1.2.3 or https://github.com/owner/repo@ref" :disabled="disabled || busy" />
      <button type="button" class="btn btn--sm" :disabled="disabled || busy || !source.trim()" @click="install">Download</button></div>
    <label class="caption"><input v-model="allowScripts" type="checkbox" :disabled="disabled || busy" /> Allow installation scripts (runs third-party code in an isolated installer)</label>
    <p v-if="installations.some(item => item.status === 'installing')" role="status" class="caption">Installing packages… This may take up to five minutes. You can leave this page.</p>
    <details class="agent-packages__library" :open="Boolean(source)">
      <summary>Downloaded artifacts & installation history</summary>
      <p class="caption dim">Choose an artifact below to add it, or download another version to update. Previous artifacts remain available for existing chats and rollback.</p>
      <article v-for="item in installations" :key="item.id" class="agent-packages__card">
        <div class="row"><div class="grow"><strong>{{ item.metadata?.resolved || item.source }}</strong><p class="caption dim">{{ item.status }} · {{ item.id.slice(0, 8) }}{{ item.allow_scripts ? ' · install scripts allowed' : '' }}</p></div>
          <button v-if="item.status === 'ready'" type="button" class="btn btn--sm" :disabled="disabled || busy || selected(item.id)" @click="add(item.id)">{{ selected(item.id) ? 'Selected' : 'Add to agent' }}</button>
          <button v-if="item.status === 'failed'" type="button" class="btn btn--sm" :disabled="disabled || busy" @click="source = item.source; allowScripts = item.allow_scripts; install()">Retry download</button>
        </div>
        <p v-if="item.error" role="alert" class="caption field-hint--bad">{{ item.error }}</p>
        <details v-if="item.metadata?.resources"><summary class="caption">Declared resources</summary><pre>{{ JSON.stringify(item.metadata.resources, null, 2) }}</pre></details>
      </article>
    </details>
    <label class="agent-packages__label" for="package-query">Search Pi packages</label>
    <input id="package-query" v-model="query" class="field" type="search" placeholder="Search npm packages…" />
    <PackageSearch :query="query" @select="source = $event" />
  </section>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../../api'
import PackageSearch from './PackageSearch.vue'
import PackageConfiguration from './PackageConfiguration.vue'
const props = defineProps({ modelValue: { type: Array, default: () => [] }, disabled: Boolean, initialSource: { type: String, default: '' } })
const emit = defineEmits(['update:modelValue', 'busy'])
const source = ref(props.initialSource), query = ref(''), allowScripts = ref(false), operationBusy = ref(false), configurationBusy = ref(false), error = ref('')
const busy = computed(() => operationBusy.value || configurationBusy.value)
watch(busy, value => emit('busy', value), { flush: 'sync' })
const installations = ref([]), revisions = ref({})
let timer, disposed = false
watch(() => props.initialSource, value => { source.value = value })
function title (id) { const item = installations.value.find(item => item.id === id); return item?.metadata?.resolved || item?.source || 'Pi package' }
function selected (id) { return props.modelValue.some(revision => revisions.value[revision]?.installation_id === id) }
function remove (id) { emit('update:modelValue', props.modelValue.filter(item => item !== id)) }
function replace (id, replacement) { emit('update:modelValue', props.modelValue.map(item => item === id ? replacement : item)) }
async function load () {
  clearTimeout(timer)
  try {
    const data = await api.packageInstallations()
    if (disposed) return
    installations.value = data.installations; error.value = ''
    await loadRevisions(props.modelValue)
    if (disposed) return
    if (data.installations.some(item => item.status === 'installing')) timer = setTimeout(load, 1500)
  } catch (failure) { if (!disposed) error.value = failure.message }
}
async function loadRevisions (ids) {
  for (const id of ids) if (!revisions.value[id]) {
    const value = await api.packageRevision(id)
    if (!disposed) revisions.value[id] = value
  }
}
watch(() => [...props.modelValue], async ids => {
  try { await loadRevisions(ids) }
  catch (failure) { if (!disposed) error.value = failure.message }
}, { immediate: true })
async function install () {
  operationBusy.value = true; error.value = ''
  try { await api.installPackage({ source: source.value.trim(), allow_scripts: allowScripts.value }); if (!disposed) await load() }
  catch (failure) { if (!disposed) error.value = failure.message }
  finally { operationBusy.value = false }
}
async function add (installationId) {
  operationBusy.value = true; error.value = ''
  try {
    const revision = await api.createPackageRevision({ installation_id: installationId })
    if (!disposed) { revisions.value[revision.id] = revision; emit('update:modelValue', [...props.modelValue, revision.id]) }
  } catch (failure) { if (!disposed) error.value = failure.message }
  finally { operationBusy.value = false }
}
onMounted(load)
onUnmounted(() => { disposed = true; clearTimeout(timer) })
</script>
<style scoped>
.agent-packages { margin: 24px 0; }
.agent-packages h3 { font-size: 14px; }
.agent-packages__card { margin: 10px 0; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); overflow-wrap: anywhere; }
.agent-packages__card strong { font-size: 12px; }
.agent-packages__card .row { flex-wrap: wrap; gap: 8px; }
.agent-packages__card pre { overflow: auto; max-height: 180px; font-size: 11px; }
.agent-packages__label { display: block; font-size: 12px; margin: 18px 0 8px; }
.agent-packages__source { gap: 8px; margin-bottom: 10px; }
.agent-packages__source input { min-width: 0; }
.agent-packages__library { margin-top: 20px; font-size: 12px; }
.agent-packages__library summary { cursor: pointer; }
</style>
