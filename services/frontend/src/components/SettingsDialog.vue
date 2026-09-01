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
            <div class="setting__label">Access token</div>
            <p class="caption dim">
              Stored in this browser only. {{ store.system.auth_enabled ? 'This instance requires one.' : 'This instance is open — no token needed.' }}
            </p>
            <div class="row" style="margin-top: 8px">
              <input v-model="token" class="field" type="password" placeholder="token" />
              <button class="btn btn--primary" @click="saveToken">Save</button>
            </div>
          </div>

          <div class="setting">
            <div class="setting__label">Default model</div>
            <p class="caption dim">
              Set by <code class="mono">AGENT_MODEL</code> on the backend. New chats start here; each
              chat can override it from its composer.
            </p>
            <div class="row" style="margin-top: 8px">
              <span class="chip chip--accent">{{ store.catalog.default_model || '—' }}</span>
              <span class="chip" :class="store.system.model_key_present ? 'chip--success' : 'chip--warning'">
                {{ store.system.model_key_present ? 'key configured' : 'no provider key' }}
              </span>
            </div>
          </div>

          <div class="setting">
            <div class="setting__label">Data</div>
            <p class="caption dim">
              Chats, runs and workflow files live in the stack's volumes. Nothing is stored in this browser
              beyond your token and the sidebar width.
            </p>
          </div>
        </template>

        <!-- agents -->
        <template v-else-if="tab === 'agents'">
          <h2 class="settings__title">Agents and models</h2>
          <p class="caption dim">
            An agent set is a container image. Every agent call starts one, runs, and disappears —
            so a set picks up new tools on its next run without a rebuild.
          </p>

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
            <div class="setting__label">Models offered by the gateway</div>
            <p v-if="!(store.catalog.models || []).length" class="caption dim">
              The gateway returned no models. Check that a provider key is set.
            </p>
            <div v-for="model in store.catalog.models || []" :key="model.id" class="line">
              <span class="grow truncate mono">{{ model.id }}</span>
              <span v-if="model.id === store.catalog.default_model" class="chip chip--accent">default</span>
            </div>
          </div>
        </template>

        <!-- mcp -->
        <template v-else-if="tab === 'mcp'">
          <h2 class="settings__title">MCP servers</h2>
          <p class="caption dim">
            Tools reach the agents through one federated endpoint on the gateway. These are the tools
            it is serving right now.
          </p>

          <div class="setting">
            <div class="row">
              <div class="setting__label grow">Available tools ({{ (store.catalog.tools || []).length }})</div>
              <button class="btn btn--sm btn--outline" :disabled="refreshing" @click="refreshCatalog">
                {{ refreshing ? 'Checking…' : 'Refresh' }}
              </button>
            </div>
            <p v-if="!(store.catalog.tools || []).length" class="caption dim">
              No MCP server answered. Check the gateway under System.
            </p>
            <div v-for="tool in store.catalog.tools || []" :key="tool.name" class="line line--stacked">
              <span class="mono">{{ tool.name }}</span>
              <span v-if="tool.description" class="caption dim">{{ tool.description }}</span>
            </div>
          </div>

          <div class="setting">
            <div class="setting__label">Add a server</div>
            <p class="caption dim">
              The gateway config is declarative and baked into its image, so edits survive restarts and
              never drift. Add a target, then rebuild and restart the gateway.
            </p>
            <div class="row" style="margin-top: 8px">
              <code class="mono grow truncate">services/agentgateway/config/config.yaml</code>
              <button class="btn btn--sm btn--outline" @click="copy(snippet)">Copy snippet</button>
            </div>
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
          <p class="caption dim">Live event stream from the backend.</p>
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
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { actions, store } from '../store'
import { auth } from '../api'

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
const refreshing = ref(false)

const snippet = `mcp:
  targets:
    - name: my-server
      mcp:
        host: http://my-server:8000/mcp`

const open = computed({
  get: () => store.settingsOpen,
  set: (value) => { store.settingsOpen = value }
})

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

async function refreshCatalog () {
  refreshing.value = true
  await actions.loadCatalog(true)
  refreshing.value = false
}

async function copy (text) {
  await navigator.clipboard.writeText(text)
  $q.notify({ message: 'Copied' })
}

watch(() => store.settingsTab, (value) => { tab.value = value })
watch(() => store.settingsOpen, (value) => {
  if (!value) return
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
  max-height: 88vh;
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

.line--stacked > .row {
  width: 100%;
}

@media (max-width: 760px) {
  .settings {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
    height: 92vh;
  }

  .settings__nav {
    flex-direction: row;
    overflow-x: auto;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }

  .settings__brand,
  .settings__version {
    display: none;
  }
}
</style>
