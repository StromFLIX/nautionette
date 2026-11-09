<template>
  <h2 class="settings__title">General</h2>
  <p class="settings__intro">Choose the starting configuration for new conversations. Saved agents inherit these defaults unless you override them.</p>
  <p v-if="loadError" class="caption field-hint--bad" role="alert">{{ loadError }} <button class="btn btn--sm" @click="load">Retry</button></p>

  <section id="default-chat-agent" class="setting general-starting-agent">
    <label for="general-starting-agent" class="setting__label">Default agent</label>
    <div class="row">
      <button id="general-starting-agent" type="button" class="field field--button grow" :disabled="saving || !loaded">
        <span class="grow truncate">{{ store.catalog.agents?.find(agent => agent.id === form.default_agent_id)?.name || 'Global defaults' }}</span><span class="material-icons" aria-hidden="true">expand_more</span>
        <AgentPicker v-model="form.default_agent_id" />
      </button>
      <RouterLink class="btn btn--outline" to="/settings/agents#agent-profiles">Manage agents<span class="material-icons" aria-hidden="true">arrow_outward</span></RouterLink>
    </div>
  </section>
  <div class="general-baseline"><h3 class="section-label">Global defaults</h3><span class="caption dim">Agents inherit these unless overridden.</span></div>
  <AgentConfigFields v-model="config" :defaults="configDefaults" prefix="default" global :disabled="saving || !loaded" />

  <section id="subagent-limits" class="setting">
    <h3 class="setting__label">Agent delegation limits · instance-wide</h3>
    <p class="caption dim">Allow subagents in each saved agent’s settings. Zero disables calls. Lower limits apply immediately; increases apply to new trees.</p>
    <div class="general-limits">
      <div><label for="subagent-depth">Maximum depth</label>
        <input id="subagent-depth" v-model.number="form.subagent_max_depth" class="field" type="number" min="0" max="10" :disabled="saving || !loaded" /></div>
      <div><label for="subagent-width">Maximum children per turn</label>
        <input id="subagent-width" v-model.number="form.subagent_max_width" class="field" type="number" min="0" max="100" :disabled="saving || !loaded" /></div>
      <div><label for="subagent-parallel">Maximum parallel calls per tree</label>
        <input id="subagent-parallel" v-model.number="form.subagent_max_parallel" class="field" type="number" min="0" max="100" :disabled="saving || !loaded" /></div>
    </div>
    <details class="integration-details">
      <summary>How delegation limits work</summary>
      <p class="caption dim">Root depth is 0; width counts all child calls per turn, including continuations. Parallelism counts unfinished subagent calls across one tree, including waiting parents (not the root). At capacity, new calls fail instead of queueing.</p>
    </details>
  </section>

  <details id="history" class="settings-disclosure general-history">
    <summary><span class="material-icons" aria-hidden="true">history</span><span class="grow">Conversation memory</span><span class="caption dim">Native Pi session</span></summary>
    <div class="general-detail">
      <p class="caption muted">Chats resume their native Pi session, including tool commands, results and extension session entries. Expanding or collapsing the timeline does not affect the agent's context.</p>
      <p class="caption dim">Pi automatically summarizes older context when needed; the original session remains available to the agent through read-only history retrieval. Send <code>/compact</code> to compact manually. Original attachments persist; other scratch files do not.</p>
      <p class="caption dim">Existing chats import their stored timeline once. Original tool output may already be truncated, and earlier compaction summaries cannot be recovered.</p>
    </div>
  </details>

  <div class="row settings__save">
    <span class="caption dim grow">Applies to new chats</span>
    <button class="btn" :disabled="saving || !loaded" @click="resetSettings">Reset</button>
    <button class="btn btn--primary" :disabled="saving || !loaded" @click="saveSettings">{{ saving ? 'Saving…' : 'Save' }}</button>
  </div>

  <section class="setting general-connection" aria-labelledby="connection-title">
    <div class="row"><h3 id="connection-title" class="setting__label grow">Connection</h3><span class="caption dim">This device</span></div>
    <p class="caption dim">Connect this browser or app to your instance. These credentials do not change anyone else’s connection.</p>
    <div class="general-detail">
      <section id="server" class="setting">
        <label for="server-url" class="setting__label">Server</label>
        <div class="row">
          <input id="server-url" v-model="serverUrl" class="field" type="url" inputmode="url" :placeholder="isNative ? 'https://nautionette.example.com' : origin" />
          <button class="btn btn--outline" aria-label="Save server" @click="saveServer">Save</button>
        </div>
      </section>
      <section id="access-token" class="setting">
        <label for="connection-token" class="setting__label">Access token</label>
        <div class="row">
          <input id="connection-token" v-model="token" class="field" type="password" placeholder="Access token" autocomplete="off" />
          <button class="btn btn--outline" aria-label="Save access token" @click="saveToken">Save</button>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import AgentConfigFields from './AgentConfigFields.vue'
import AgentPicker from '../AgentPicker.vue'
import { DEFAULT_CONFIG_KEYS as CONFIG_KEYS, globalChatConfig } from '../../agent-config'
import { actions, store } from '../../store'
import { api, auth, isNative, server } from '../../api'

const $q = useQuasar()
const origin = window.location.origin
const token = ref(auth.token)
const serverUrl = ref(server.url)
const saving = ref(false)
const loaded = ref(false)
const loadError = ref('')
const form = reactive({ default_agent_id: null, subagent_max_depth: 3, subagent_max_width: 5, subagent_max_parallel: 4 })
const config = ref(globalChatConfig(store.catalog))
const configDefaults = ref(globalChatConfig(store.catalog))
function apply (data) {
  for (const key of Object.keys(form)) {
    if (Object.hasOwn(data.settings || {}, key)) form[key] = data.settings[key]
  }
  config.value = globalChatConfig(data.settings || {})
  configDefaults.value = globalChatConfig(data.defaults || {})
}
function saveToken () { actions.setToken(token.value.trim()); $q.notify({ type: 'positive', message: 'Token saved' }) }
function saveServer () {
  actions.setServer(serverUrl.value)
  $q.notify({ type: 'positive', message: serverUrl.value.trim() ? 'Server saved' : 'Using this origin' })
}
async function persist (payload) {
  saving.value = true
  try {
    const saved = await api.saveSettings(payload)
    apply(saved)
    actions.applyAgentSettings(saved.settings)
    await actions.loadCatalog(true)
    $q.notify({ type: 'positive', message: 'Saved' })
  } catch (error) { $q.notify({ type: 'negative', message: error.message }) }
  finally { saving.value = false }
}
function saveSettings () {
  return persist({ ...form, ...Object.fromEntries(CONFIG_KEYS.map(key => [`default_${key}`, config.value[key]])) })
}
function resetSettings () {
  return persist({ ...Object.fromEntries(Object.keys(form).map(key => [key, null])), ...Object.fromEntries(CONFIG_KEYS.map(key => [`default_${key}`, null])) })
}
async function load () {
  loadError.value = ''
  try { apply(await api.settings()); loaded.value = true }
  catch (error) { loadError.value = error.message }
}
onMounted(load)
</script>

<style scoped>
.general-starting-agent .setting__label { display: block; margin-bottom: 10px; }
.general-starting-agent .row { gap: 12px; }
.general-starting-agent .field { min-width: 0; width: auto; min-height: 42px; }
.general-starting-agent .btn { flex: none; }
.general-starting-agent .material-icons { font-size: 0.9375rem; }
.general-baseline { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; margin-top: 28px; }
.general-baseline h3 { margin: 0; }
.general-detail { padding: 0 0 16px 24px; }
.general-connection > .general-detail { padding: 20px 0 0; }
.general-limits { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 16px; }
.general-limits label { display: block; min-height: 2.8em; margin-bottom: 6px; font-size: 0.75rem; line-height: 1.4; color: var(--text-muted); }
.general-detail .setting__label { display: block; margin-bottom: 8px; }
.general-detail > p { margin: 10px 0 0; }
.general-detail .row > .field { flex: 1; }
.general-detail .row > .btn { flex: none; }
.general-detail .setting { margin-top: 18px; }
.general-detail .setting:first-child { margin-top: 0; }
.settings__save { margin: 20px 0 36px; }
@media (max-width: 600px) {
  .general-starting-agent .row { flex-wrap: wrap; }
  .general-detail { padding-left: 20px; }
  .general-limits { grid-template-columns: 1fr; }
  .general-limits label { min-height: 0; }
  .settings__save { gap: 4px; }
}
</style>
