<template>
  <q-dialog v-model="open" @hide="actions.closeSettings()">
    <q-card class="settings">
      <aside class="settings__nav">
        <div class="settings__brand">
          <img src="/favicon.svg" width="20" height="20" alt="" />
          <span>Settings</span>
        </div>
        <button
          v-for="item in tabs" :key="item.key" class="settings__tab"
          :class="{ 'settings__tab--active': tab === item.key }" @click="tab = item.key"
        >
          <span class="material-icons">{{ item.icon }}</span>{{ item.label }}
        </button>
        <div class="settings__version caption dim">v{{ store.system.version || 'dev' }}</div>
      </aside>

      <div class="settings__body scroll-y">
        <!-- general -->
        <template v-if="tab === 'general'">
          <h2 class="settings__title">General</h2>

          <div class="setting">
            <div class="setting__label">Server</div>
            <div class="row">
              <input
                v-model="serverUrl" class="field" type="url" inputmode="url"
                :placeholder="isNative ? 'https://nautionette.example.com' : origin"
              />
              <button class="btn btn--primary" @click="saveServer">Save</button>
            </div>
          </div>

          <div class="setting">
            <div class="setting__label">Access token</div>
            <div class="row">
              <input v-model="token" class="field" type="password" placeholder="token" />
              <button class="btn btn--primary" @click="saveToken">Save</button>
            </div>
          </div>

          <div class="setting">
            <div class="setting__label">Default model</div>
            <div class="row">
              <button class="field field--button">
                <span class="grow truncate">{{ form.default_model || '—' }}</span>
                <span class="material-icons" style="font-size: 17px">expand_more</span>
                <ModelPicker
                  :model-value="form.default_model"
                  @update:model-value="form.default_model = $event"
                />
              </button>
            </div>
          </div>

          <div class="setting">
            <div class="setting__label">Default agent set</div>
            <div class="row">
              <select v-model="form.default_agent_set" class="field">
                <option v-for="set in store.catalog.agent_sets || []" :key="set.name" :value="set.name">
                  {{ set.name }}
                </option>
              </select>
            </div>
          </div>

          <div class="setting">
            <div class="setting__label">History sent to each call</div>
            <div class="row">
              <select v-model="historyMode" class="field" style="max-width: 190px">
                <option value="auto">From the model</option>
                <option value="fixed">A fixed number</option>
              </select>
              <input
                v-if="historyMode === 'fixed'" v-model.number="form.history_chars"
                class="field" type="number" min="2000" step="10000"
              />
              <span class="caption dim">{{ contextHint }}</span>
            </div>
          </div>

          <div class="row settings__save">
            <span class="caption dim grow">Default: {{ defaults.default_model }}</span>
            <button class="btn" @click="resetSettings">Reset</button>
            <button class="btn btn--primary" :disabled="saving" @click="saveSettings">
              {{ saving ? 'Saving…' : 'Save' }}
            </button>
          </div>
        </template>

        <!-- agents -->
        <template v-else-if="tab === 'agents'">
          <h2 class="settings__title">Agents and models</h2>

          <div class="setting">
            <div class="setting__label">Agent sets</div>
            <div v-for="set in store.catalog.agent_sets || []" :key="set.name" class="line">
              <span class="grow truncate">{{ set.name }}</span>
              <span v-if="set.image" class="caption dim mono truncate">{{ set.image }}</span>
              <span class="chip" :class="set.ready === false ? 'chip--warning' : 'chip--success'">
                {{ set.ready === false ? 'building' : 'ready' }}
              </span>
            </div>
          </div>

          <div class="setting">
            <div class="setting__label">Models reachable through the gateway</div>
            <template v-for="gateway in modelGroups" :key="gateway.name">
              <p class="caption dim" style="margin-top: 10px">
                via <code class="mono">{{ gateway.name }}</code> · {{ gateway.total }} models
              </p>
              <div v-for="provider in gateway.providers" :key="provider.name" class="line">
                <span class="grow truncate">{{ provider.name }}</span>
                <span v-if="provider.hasDefault" class="chip chip--accent">default</span>
                <span class="caption dim">{{ provider.count }}</span>
              </div>
            </template>
          </div>
        </template>

        <!-- mcp -->
        <template v-else-if="tab === 'mcp'">
          <h2 class="settings__title">MCP servers</h2>

          <div class="setting">
            <div class="row">
              <div class="setting__label grow">Connected servers</div>
              <button class="btn btn--sm btn--outline" :disabled="refreshing" @click="refreshCatalog">
                {{ refreshing ? 'Checking…' : 'Refresh' }}
              </button>
            </div>
            <p v-if="!toolGroups.length" class="caption dim">No MCP server answered.</p>
            <template v-for="group in toolGroups" :key="group.name">
              <div class="line">
                <span class="grow truncate">{{ group.name }}</span>
                <span v-if="group.host" class="caption dim mono truncate">{{ group.host }}</span>
                <span class="chip chip--success">{{ group.tools.length }} tools</span>
              </div>
              <div v-for="tool in group.tools" :key="tool.name" class="line line--stacked line--nested">
                <span class="mono">{{ tool.name }}</span>
                <span v-if="tool.description" class="caption dim">{{ tool.description }}</span>
              </div>
            </template>
          </div>

          <div class="setting">
            <div class="row">
              <div class="setting__label grow">Add a server</div>
              <button class="btn btn--sm btn--outline" @click="copy(snippet)">Copy</button>
            </div>
            <code class="mono dim">services/agentgateway/config/config.yaml</code>
            <pre class="code" style="margin-top: 8px">{{ snippet }}</pre>
          </div>
        </template>

        <!-- system -->
        <template v-else-if="tab === 'system'">
          <h2 class="settings__title">System</h2>
          <div class="setting">
            <div class="row">
              <div class="setting__label grow">Components</div>
              <button class="btn btn--sm btn--outline" @click="actions.loadSystem()">Refresh</button>
            </div>
            <div v-for="component in store.system.components || []" :key="component.name" class="line line--stacked">
              <div class="row">
                <span class="dot" :class="component.status === 'ok' ? 'dot--ok' : 'dot--bad'" />
                <span class="grow">{{ component.name }}</span>
                <span class="chip" :class="component.status === 'ok' ? 'chip--success' : 'chip--danger'">
                  {{ component.status }}
                </span>
              </div>
              <span class="caption dim mono clamp-2">{{ render(component.detail) }}</span>
            </div>
          </div>
        </template>

        <!-- activity -->
        <template v-else>
          <h2 class="settings__title">Activity</h2>
          <div class="setting">
            <div v-for="(event, index) in store.events" :key="index" class="line line--stacked">
              <div class="row">
                <span class="mono grow truncate">{{ event.kind }}</span>
                <span class="caption dim">{{ new Date(event.at * 1000).toLocaleTimeString() }}</span>
              </div>
              <span class="caption dim mono truncate">{{ summary(event) }}</span>
            </div>
            <p v-if="!store.events.length" class="caption dim">Waiting for the system to do something.</p>
          </div>
        </template>
      </div>

      <button class="settings__close btn btn--icon" @click="actions.closeSettings()">
        <span class="material-icons">close</span>
      </button>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import ModelPicker from './ModelPicker.vue'
import { compactChars } from '../format'
import { actions, historyBudget, store } from '../store'
import { api, auth, isNative, server } from '../api'

const $q = useQuasar()

const tabs = [
  { key: 'general', label: 'General', icon: 'tune' },
  { key: 'agents', label: 'Agents', icon: 'smart_toy' },
  { key: 'mcp', label: 'MCP servers', icon: 'handyman' },
  { key: 'system', label: 'System', icon: 'monitor_heart' },
  { key: 'activity', label: 'Activity', icon: 'bolt' }
]

const tab = ref(store.settingsTab)
const token = ref(auth.token)
const serverUrl = ref(server.url)
const refreshing = ref(false)
const saving = ref(false)
const form = reactive({ default_model: '', default_agent_set: '', history_chars: 0 })
const defaults = reactive({ default_model: '', default_agent_set: '', history_chars: 0 })
const origin = window.location.origin
const historyMode = ref('auto')

const contextHint = computed(() => {
  if (historyMode.value === 'fixed') {
    return `${Math.round((form.history_chars || 0) / 4000)}k tokens`
  }
  const model = (store.catalog.models || []).find((item) => item.id === form.default_model)
  if (!model?.context_length) return 'this model publishes no window; the fallback is used'
  return `${compactChars(model.context_length)} token window \u2192 ${compactChars(historyBudget(form.default_model))} chars`
})

const snippet = `mcp:
  targets:
    - name: my-server
      mcp:
        host: http://my-server:8000/mcp`

const open = computed({
  get: () => store.settingsOpen,
  set: (value) => { store.settingsOpen = value }
})

const modelGroups = computed(() => {
  const byGateway = new Map()
  for (const model of store.catalog.models || []) {
    const gateway = model.gateway || 'gateway'
    if (!byGateway.has(gateway)) byGateway.set(gateway, new Map())
    const byProvider = byGateway.get(gateway)
    const provider = model.provider || 'other'
    byProvider.set(provider, [...(byProvider.get(provider) || []), model])
  }
  return [...byGateway].map(([name, byProvider]) => ({
    name,
    total: [...byProvider.values()].reduce((sum, list) => sum + list.length, 0),
    providers: [...byProvider]
      .map(([provider, list]) => ({
        name: provider,
        count: list.length,
        hasDefault: list.some((model) => model.id === store.catalog.default_model)
      }))
      .sort((a, b) => b.count - a.count)
  }))
})

const toolGroups = computed(() =>
  (store.catalog.tool_servers || []).map((server) => ({
    ...server,
    tools: (store.catalog.tools || []).filter((tool) => tool.server === server.name)
  })))

function render (detail) {
  return typeof detail === 'string' ? detail : JSON.stringify(detail)
}

function summary (event) {
  const { kind, at, ...rest } = event
  return JSON.stringify(rest).slice(0, 200)
}

function saveToken () {
  actions.setToken(token.value.trim())
  $q.notify({ type: 'positive', message: 'Token saved' })
}

function saveServer () {
  actions.setServer(serverUrl.value)
  $q.notify({ type: 'positive', message: serverUrl.value.trim() ? 'Server saved' : 'Using this origin' })
}

async function refreshCatalog () {
  refreshing.value = true
  await actions.loadCatalog(true)
  refreshing.value = false
}

async function copy (text) {
  await navigator.clipboard.writeText(text)
  $q.notify({ message: 'Copied' })
}

async function loadSettings () {
  const data = await api.settings()
  Object.assign(form, data.settings)
  Object.assign(defaults, data.defaults)
  historyMode.value = data.settings.history_chars > 0 ? 'fixed' : 'auto'
}

async function saveSettings () {
  saving.value = true
  try {
    const data = await api.saveSettings({
      ...form,
      history_chars: historyMode.value === 'auto' ? 0 : form.history_chars
    })
    Object.assign(form, data.settings)
    historyMode.value = data.settings.history_chars > 0 ? 'fixed' : 'auto'
    await actions.loadCatalog(true)
    $q.notify({ type: 'positive', message: 'Saved' })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    saving.value = false
  }
}

async function resetSettings () {
  const data = await api.saveSettings({ default_model: null, default_agent_set: null, history_chars: null })
  Object.assign(form, data.settings)
  historyMode.value = 'auto'
  await actions.loadCatalog(true)
}

watch(() => store.settingsTab, (value) => { tab.value = value })
watch(() => store.settingsOpen, (value) => {
  if (!value) return
  loadSettings()
  if (tab.value === 'activity') actions.loadEvents()
})
</script>

<style scoped>
.settings {
  position: relative;
  display: grid;
  grid-template-columns: 200px 1fr;
  width: 880px;
  max-width: 94vw;
  height: 620px;
  max-height: 88dvh;
  overflow: hidden;
}

.settings__nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px 8px;
  background: var(--surface-rail);
  border-right: 1px solid var(--border);
}

.settings__brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px 14px;
  font-size: 14px;
  font-weight: 650;
}

.settings__tab {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 10px;
  border: none;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-muted);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background var(--transition), color var(--transition);
}

.settings__tab:hover {
  background: var(--surface-hover);
  color: var(--text);
}

.settings__tab--active {
  background: var(--accent-soft);
  color: var(--accent-hover);
}

.settings__tab .material-icons {
  font-size: 17px;
}

.settings__version {
  margin-top: auto;
  padding: 8px 10px 2px;
}

.settings__body {
  padding: 22px 26px 32px;
}

.settings__title {
  margin: 0 0 4px;
  font-size: 17px;
  font-weight: 650;
  line-height: 1.35;
  letter-spacing: -0.01em;
}

.settings__close {
  position: absolute;
  top: 10px;
  right: 10px;
}

.setting {
  margin-top: 22px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}

.setting:first-of-type {
  border-top: none;
  padding-top: 0;
}

.setting__label {
  margin-bottom: 4px;
  font-size: 13px;
  font-weight: 600;
}

.settings__save {
  position: sticky;
  bottom: -32px;
  margin-top: 24px;
  padding: 12px 0;
  border-top: 1px solid var(--border);
  background: var(--surface-overlay);
}

.field--button {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  text-align: left;
}

select.field {
  appearance: none;
}

.setting p {
  margin: 0;
}

.line {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  margin-top: 4px;
  border-radius: var(--radius-sm);
  background: var(--surface-panel);
  font-size: 13px;
  min-width: 0;
}

.line--stacked {
  flex-direction: column;
  align-items: stretch;
  gap: 2px;
}

.line--nested {
  margin-left: 14px;
  background: transparent;
}

.line--stacked > .row {
  width: 100%;
}

@media (max-width: 760px) {
  .settings {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
    width: 100%;
    max-width: 100%;
    height: calc(100dvh - max(24px, env(safe-area-inset-top)) - max(24px, env(safe-area-inset-bottom)));
    max-height: 100%;
  }

  .settings__nav {
    flex-direction: row;
    padding-right: 44px;
    overflow-x: auto;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }

  .settings__brand,
  .settings__version {
    display: none;
  }

  .settings__body {
    padding: 18px 16px max(24px, env(safe-area-inset-bottom));
  }

  .settings__close {
    top: 8px;
    right: 8px;
  }

  .setting > .row,
  .settings__save {
    flex-wrap: wrap;
  }

  .settings__save .grow {
    flex-basis: 100%;
  }
}

@media (max-width: 420px) {
  .settings__tab {
    flex: none;
    padding: 8px;
  }

  .settings__tab .material-icons {
    display: none;
  }
}
</style>
