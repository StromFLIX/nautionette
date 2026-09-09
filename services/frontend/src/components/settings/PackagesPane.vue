<template>
  <h2 id="pi-packages" class="settings__title">Extension library</h2>
  <p class="settings__intro">Install extensions, skills and prompts once for the workspace. Then enable them in an agent or in the chat’s configuration. No agent is needed to install.</p>
  <div class="library-tabs" role="group" aria-label="Extension library view">
    <button type="button" class="btn" :class="{ 'btn--primary': tab === 'installed' }" :aria-pressed="tab === 'installed'" @click="tab = 'installed'">Installed <span class="caption">{{ installations.filter(item => item.status === 'ready').length }}</span></button>
    <button type="button" class="btn" :class="{ 'btn--primary': tab === 'discover' }" :aria-pressed="tab === 'discover'" @click="tab = 'discover'">Discover packages</button>
  </div>
  <p v-if="error" class="caption field-hint--bad" role="alert">{{ error }} <button type="button" class="btn btn--sm" @click="load">Retry loading</button></p>
  <p v-if="loading" class="caption dim" role="status">Loading library…</p>
  <section v-show="tab === 'installed'" aria-label="Installed packages">
    <div v-if="!loading && !error && !installations.length" class="library-empty">
      <span class="material-icons" aria-hidden="true">extension</span><h3>Your extension library is empty</h3>
      <p class="caption dim">Find a package or install from npm or a public GitHub URL. Installing does not enable it in any chat.</p>
      <button type="button" class="btn btn--primary" @click="tab = 'discover'">Find extensions</button>
    </div>
    <article v-for="item in installations" :key="item.id" class="library-card">
      <div class="library-card__head"><span class="material-icons" aria-hidden="true">extension</span>
        <div class="grow"><h3>{{ item.metadata?.resolved || item.source }}</h3><p class="caption dim">{{ resources(item) }}</p></div>
        <span class="chip" :class="item.status === 'ready' ? 'chip--success' : 'chip--warning'">{{ item.status === 'ready' ? 'Installed' : item.status === 'installing' ? 'Installing…' : 'Failed' }}</span>
      </div>
      <p v-if="item.status === 'installing'" class="caption dim" role="status">Downloading and installing dependencies. This can take up to five minutes; you can leave this page.</p>
      <p v-if="item.error" class="caption field-hint--bad" role="alert">{{ item.error }}</p>
      <button v-if="item.status === 'failed'" type="button" class="btn btn--sm" :disabled="busy" @click="install(item.source, item.allow_scripts)">Retry installation</button>
      <template v-if="item.status === 'ready'">
        <p class="caption dim">Available in the extension picker. Not enabled automatically.</p>
        <PackageConfiguration v-if="revisions[item.default_revision_id]" :revision="revisions[item.default_revision_id]" library :disabled="busy" @replace="configured(item, $event)" @busy="busy = $event" />
        <button v-else type="button" class="btn btn--sm" @click="load">Reload configuration</button>
        <details class="library-card__details"><summary>Installation details</summary><p class="caption dim">{{ item.source }} · {{ item.allow_scripts ? 'Install scripts allowed' : 'Install scripts disabled' }}</p>
          <p class="caption dim">Installing another version keeps this one available for pinned agents and chats. Compatibility is not guaranteed by installation.</p>
          <pre v-if="item.metadata?.resources">{{ JSON.stringify(item.metadata.resources, null, 2) }}</pre>
        </details>
      </template>
    </article>
    <p v-if="installations.length" class="caption dim library-next">Enable installed packages in <RouterLink to="/settings/agents#agent-profiles">Agents</RouterLink> or use <strong>Extensions</strong> alongside Tools and Projects below a message.</p>
  </section>
  <section v-if="tab === 'discover'" aria-label="Discover packages">
    <p class="caption library-warning">Third-party extensions run with the agent’s access to tools, projects and supplied secrets. Only install code you trust. Terminal-only UI, interactive setup and background work across turns are not supported.</p>
    <label class="setting__label" for="package-query">Search packages</label>
    <input id="package-query" v-model="query" class="field library-query" type="search" placeholder="Search the Pi npm catalog…" />
    <PackageSearch :query="query" action-label="Install" :disabled="busy" :installations="installations" @select="install($event)" />
    <details class="settings-disclosure library-source" :open="Boolean(source)">
      <summary>Install from npm or public GitHub</summary>
      <div class="library-source__body">
        <label class="setting__label" for="package-source">Package source</label>
        <div class="library-source__input"><input id="package-source" v-model="source" class="field grow" placeholder="npm:package@version or https://github.com/owner/repo@ref" :disabled="busy" />
          <button type="button" class="btn btn--primary" :disabled="busy || !source.trim()" @click="install(source, allowScripts)">Install source</button></div>
        <label class="caption"><input v-model="allowScripts" type="checkbox" :disabled="busy" /> Allow installation scripts (executes third-party install code)</label>
      </div>
    </details>
  </section>
</template>
<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../../api'
import PackageSearch from './PackageSearch.vue'
import PackageConfiguration from './PackageConfiguration.vue'
const route = useRoute()
const tab = ref('installed'), installations = ref([]), revisions = ref({}), source = ref(''), query = ref(''), allowScripts = ref(false), busy = ref(false), loading = ref(true), error = ref('')
let timer, disposed = false
watch(() => route.query.source, value => { if (typeof value === 'string' && value) { source.value = value; tab.value = 'discover' } }, { immediate: true })
function resources (item) { return Object.keys(item.metadata?.resources || {}).join(' · ') || item.source }
function configured (item, revision) { revisions.value[revision.id] = revision; item.default_revision_id = revision.id }
async function load () {
  clearTimeout(timer)
  try {
    const data = await api.packageInstallations()
    const values = await Promise.all(data.installations.filter(item => item.default_revision_id && !revisions.value[item.default_revision_id]).map(item => api.packageRevision(item.default_revision_id)))
    if (disposed) return
    installations.value = data.installations
    for (const revision of values) revisions.value[revision.id] = revision
    error.value = ''
    if (data.installations.some(item => item.status === 'installing')) timer = setTimeout(load, 1500)
  } catch (failure) { if (!disposed) error.value = failure.message }
  finally { if (!disposed) loading.value = false }
}
async function install (value, scripts = false) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    await api.installPackage({ source: value.trim(), allow_scripts: scripts })
    if (!disposed) { tab.value = 'installed'; await load() }
  } catch (failure) { if (!disposed) error.value = failure.message }
  finally { busy.value = false }
}
onMounted(load)
onUnmounted(() => { disposed = true; clearTimeout(timer) })
</script>
<style scoped>
.library-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin: 24px 0; }
.library-tabs .btn { gap: 8px; }
.library-empty { text-align: center; padding: 36px 16px; border: 1px dashed var(--border-strong); border-radius: var(--radius-md); }
.library-empty > .material-icons { color: var(--accent); font-size: 2rem; }
.library-empty h3 { font-size: 1rem; }
.library-empty p { max-width: 400px; margin: 12px auto 20px; }
.library-card { margin: 16px 0; padding: 18px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-panel); overflow-wrap: anywhere; }
.library-card__head { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.library-card__head > .material-icons { color: var(--accent); font-size: 1.375rem; }
.library-card__head .grow { min-width: 0; flex-basis: 50%; }
.library-card h3 { margin: 0; font-size: 0.875rem; }
.library-card p { margin: 10px 0; }
.library-card__head p { margin: 4px 0 0; }
.library-card__details { margin-top: 16px; font-size: 0.75rem; }
.library-card__details summary { cursor: pointer; color: var(--text-muted); }
.library-card pre { max-height: 180px; overflow: auto; font-size: 0.6875rem; }
.library-next { margin-top: 24px; line-height: 1.7; }
.library-warning { padding: 12px 14px; border-radius: var(--radius-sm); background: var(--warning-soft); color: var(--text-muted); margin: 0 0 20px; }
.library-query { width: 100%; }
.library-source { margin-top: 24px; }
.library-source__body { padding: 4px 16px 16px; }
.library-source__input { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0 14px; }
.library-source__input input { min-width: 0; flex-basis: 250px; }
.library-source input[type=checkbox] { accent-color: var(--accent); }
@media (max-width: 640px) { .library-card { padding: 14px; } }
</style>
