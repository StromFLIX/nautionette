<template>
  <h2 class="settings__title">General</h2>
  <p class="settings__intro">Defaults for new chats.</p>
  <p v-if="loadError" class="caption field-hint--bad" role="alert">{{ loadError }} <button class="btn btn--sm" @click="load">Retry</button></p>

  <div class="general-defaults">
    <section id="default-model" class="setting">
      <label for="general-model" class="setting__label">Default model</label>
      <button id="general-model" class="field field--button">
        <span class="grow truncate">{{ form.default_model || 'Choose a model' }}</span>
        <span class="material-icons" aria-hidden="true">expand_more</span>
        <ModelPicker :model-value="form.default_model" @update:model-value="form.default_model = $event" />
      </button>
    </section>
    <section id="default-agent" class="setting">
      <label for="general-agent" class="setting__label">Default agent set</label>
      <select id="general-agent" v-model="form.default_agent_set" class="field">
        <option v-for="set in store.catalog.agent_sets || []" :key="set.name" :value="set.name">{{ set.name }}</option>
      </select>
    </section>
  </div>

  <details id="history" class="settings-disclosure general-history">
    <summary><span class="material-icons" aria-hidden="true">history</span><span class="grow">History budget</span><span class="caption dim">{{ historyMode === 'auto' ? 'Automatic' : 'Custom' }}</span></summary>
    <div class="general-detail">
      <label for="history-mode" class="setting__label">Transcript limit</label>
      <div class="row">
        <select id="history-mode" v-model="historyMode" class="field">
          <option value="auto">From the model</option>
          <option value="fixed">A fixed number</option>
        </select>
        <input v-if="historyMode === 'fixed'" v-model.number="form.history_chars" class="field" type="number" min="2000" step="10000" aria-label="History character limit" />
      </div>
      <p class="caption muted">{{ contextHint }}</p>
      <p class="caption dim">Trims the transcript only. The context meter includes instructions and tools.</p>
    </div>
  </details>

  <div class="row settings__save">
    <span class="caption dim grow">Applies to new chats</span>
    <button class="btn" :disabled="saving || !loaded" @click="resetSettings">Reset</button>
    <button class="btn btn--primary" :disabled="saving || !loaded" @click="saveSettings">{{ saving ? 'Saving…' : 'Save' }}</button>
  </div>

  <details class="settings-disclosure general-connection">
    <summary><span class="material-icons" aria-hidden="true">link</span><span class="grow">Connection</span><span class="caption dim">This device</span></summary>
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
  </details>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import ModelPicker from '../ModelPicker.vue'
import { compactChars } from '../../format'
import { actions, historyBudget, store } from '../../store'
import { api, auth, isNative, server } from '../../api'

const $q = useQuasar()
const origin = window.location.origin
const token = ref(auth.token)
const serverUrl = ref(server.url)
const saving = ref(false)
const loaded = ref(false)
const loadError = ref('')
const historyMode = ref('auto')
const form = reactive({ default_model: '', default_agent_set: '', history_chars: 0 })
const contextHint = computed(() => {
  if (historyMode.value === 'fixed') return `${compactChars(form.history_chars || 0)} characters of history`
  const model = (store.catalog.models || []).find(item => item.id === form.default_model)
  if (!model?.context_length) return 'No window published. The instance fallback is used.'
  return `${compactChars(model.context_length)} token window → ${compactChars(historyBudget(form.default_model))} characters`
})
function apply (data) {
  for (const key of Object.keys(form)) {
    if (Object.hasOwn(data.settings || {}, key)) form[key] = data.settings[key]
  }
  historyMode.value = form.history_chars > 0 ? 'fixed' : 'auto'
}
function saveToken () { actions.setToken(token.value.trim()); $q.notify({ type: 'positive', message: 'Token saved' }) }
function saveServer () {
  actions.setServer(serverUrl.value)
  $q.notify({ type: 'positive', message: serverUrl.value.trim() ? 'Server saved' : 'Using this origin' })
}
async function persist (payload) {
  saving.value = true
  try {
    apply(await api.saveSettings(payload))
    await actions.loadCatalog(true)
    $q.notify({ type: 'positive', message: 'Saved' })
  } catch (error) { $q.notify({ type: 'negative', message: error.message }) }
  finally { saving.value = false }
}
function saveSettings () {
  if (historyMode.value === 'fixed' && (!Number.isInteger(form.history_chars) || form.history_chars < 2000)) {
    $q.notify({ type: 'negative', message: 'History limit must be at least 2,000 characters.' })
    return
  }
  return persist({ ...form, history_chars: historyMode.value === 'auto' ? 0 : form.history_chars })
}
function resetSettings () { return persist({ default_model: null, default_agent_set: null, history_chars: null }) }
async function load () {
  loadError.value = ''
  try { apply(await api.settings()); loaded.value = true }
  catch (error) { loadError.value = error.message }
}
onMounted(load)
</script>

<style scoped>
.general-defaults { display: grid; grid-template-columns: 1.2fr 1fr; gap: 24px; margin: 28px 0; }
.general-defaults .setting { margin: 0; padding: 0; border: none; }
.general-defaults .setting__label { display: block; margin-bottom: 10px; }
.general-defaults .field { min-height: 42px; }
.general-defaults .material-icons { font-size: 18px; }
.general-detail { padding: 8px 18px 20px; }
.general-detail .setting__label { display: block; margin-bottom: 8px; }
.general-detail > p { margin: 10px 0 0; }
.general-detail .row > .field { flex: 1; }
.general-detail .row > .btn { flex: none; }
.general-detail .setting { margin-top: 18px; }
.general-detail .setting:first-child { margin-top: 0; }
.settings__save { margin: 20px 0 36px; }
@media (max-width: 600px) {
  .general-defaults { grid-template-columns: minmax(0, 1fr); gap: 20px; }
  .general-detail { padding-inline: 12px; }
  .settings__save { gap: 4px; }
}
</style>
