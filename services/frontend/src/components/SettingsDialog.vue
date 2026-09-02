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
            <div class="row integration-head">
              <div class="setting__label grow">Model integrations</div>
              <button
                class="btn btn--sm btn--outline" :disabled="integrationLoading"
                @click="refreshIntegrations"
              >
                {{ integrationLoading ? 'Checking…' : 'Refresh models' }}
              </button>
              <button
                class="btn btn--sm btn--primary"
                :disabled="!integrations.writable || !availableIntegrations.length"
                @click="toggleIntegrationAdd"
              >
                Add integration
              </button>
            </div>
            <p class="caption dim">
              Provider routes live in agentgateway. Their models are discovered automatically and
              labeled by both integration and model vendor in every picker.
            </p>

            <div v-if="integrationAdding" class="integration-add">
              <template v-if="!selectedIntegration">
                <div class="section-label">Available integrations</div>
                <button
                  v-for="integration in availableIntegrations" :key="integration.type"
                  class="integration-option" @click="chooseIntegration(integration)"
                >
                  <span class="material-icons">add_circle_outline</span>
                  <span class="grow">
                    <strong>{{ integration.name }}</strong>
                    <span class="caption dim">{{ integration.description }}</span>
                  </span>
                </button>
              </template>
              <template v-else>
                <div class="row">
                  <button class="btn btn--icon btn--sm" @click="backFromIntegrationForm">
                    <span class="material-icons">arrow_back</span>
                  </button>
                  <strong class="grow">
                    {{ selectedIntegration.configured ? 'Configure' : 'Add' }} {{ selectedIntegration.name }}
                  </strong>
                </div>
                <p class="caption dim">{{ selectedIntegration.description }}</p>
                <label v-for="field in selectedIntegration.fields" :key="field.key" class="integration-field">
                  <span class="caption">{{ field.label }}{{ field.optional ? ' (optional)' : '' }}</span>
                  <input
                    v-model="integrationDraft[field.key]" class="field mono"
                    :type="field.kind === 'secret' ? 'password' : 'text'"
                    :autocomplete="field.kind === 'secret' ? 'new-password' : 'off'"
                    :placeholder="secretPlaceholder(field) || field.placeholder || ''"
                    :disabled="selectedIntegration.configured && field.key === 'slug'"
                  />
                  <span class="caption dim">{{ field.help }}</span>
                </label>
                <p class="caption dim">
                  Credentials stay on agentgateway and are never returned to this page.
                </p>
                <div class="row integration-actions">
                  <button class="btn btn--sm" @click="cancelIntegrationAdd">Cancel</button>
                  <button
                    class="btn btn--sm btn--primary"
                    :disabled="Boolean(integrationBusy) || !integrationDraftValid"
                    @click="addIntegration"
                  >
                    {{ integrationBusy
                      ? 'Saving…'
                      : selectedIntegration.configured ? 'Save configuration' : 'Add integration' }}
                  </button>
                </div>
              </template>
            </div>

            <p v-if="!configuredIntegrations.length && !integrationLoading" class="caption dim integration-empty">
              No model integration is configured.
            </p>
            <div
              v-for="integration in configuredIntegrations" :key="integration.instance"
              class="integration-card"
            >
              <div class="row">
                <span class="material-icons integration-card__icon">hub</span>
                <strong class="grow">{{ integration.name }}</strong>
                <span
                  class="chip"
                  :class="integration.discovery.ok ? 'chip--success' : 'chip--warning'"
                >
                  {{ integration.discovery.ok ? `${integration.model_count} models` : 'needs attention' }}
                </span>
              </div>
              <p class="caption dim">{{ integration.description }}</p>
              <div class="integration-meta caption dim">
                <span>route <code class="mono">{{ integration.model_match }}</code></span>
                <span>{{ credentialLabel(integration.credential) }}</span>
              </div>
              <p v-if="!integration.discovery.ok" class="caption integration-warning">
                {{ integration.discovery.message }}
              </p>
              <div v-if="integrationTests[integration.instance]" class="line">
                <span
                  class="dot"
                  :class="integrationTests[integration.instance].ok ? 'dot--ok' : 'dot--bad'"
                />
                <span class="caption grow">{{ integrationTests[integration.instance].message }}</span>
                <span v-if="integrationTests[integration.instance].status" class="caption dim">
                  HTTP {{ integrationTests[integration.instance].status }}
                </span>
              </div>
              <div class="row integration-actions">
                <button
                  v-if="integration.fields.length" class="btn btn--sm"
                  :disabled="Boolean(integrationBusy)" @click="chooseIntegration(integration)"
                >
                  Configure
                </button>
                <button
                  class="btn btn--sm btn--outline" :disabled="Boolean(integrationBusy)"
                  @click="testIntegration(integration)"
                >
                  {{ integrationBusy === integration.instance && integrationAction === 'test'
                    ? 'Testing…' : 'Test' }}
                </button>
                <span class="grow" />
                <button
                  class="btn btn--sm btn--danger" :disabled="Boolean(integrationBusy)"
                  @click="removeIntegration(integration)"
                >
                  Remove
                </button>
              </div>
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
            <div class="row integration-head">
              <div class="setting__label grow">Servers</div>
              <button
                class="btn btn--sm btn--outline" :disabled="mcpLoading || refreshing"
                @click="refreshMcp"
              >
                {{ mcpLoading || refreshing ? 'Checking…' : 'Refresh' }}
              </button>
              <button
                class="btn btn--sm btn--primary" :disabled="!mcp.writable || mcpAdding"
                @click="startMcpAdd"
              >
                Add server
              </button>
            </div>
            <p class="caption dim">
              Every server is federated onto one endpoint in agentgateway. Its tools arrive
              prefixed with the server name and show up in the tool picker straight away.
            </p>

            <div v-if="mcpAdding" class="integration-add">
              <strong>{{ mcpEditing ? `Configure ${mcpEditing}` : 'Add a server' }}</strong>
              <label v-for="field in mcp.fields" :key="field.key" class="integration-field">
                <span class="caption">{{ field.label }}{{ field.optional ? ' (optional)' : '' }}</span>
                <input
                  v-model="mcpDraft[field.key]" class="field mono"
                  :type="field.kind === 'secret' ? 'password' : 'text'"
                  :autocomplete="field.kind === 'secret' ? 'new-password' : 'off'"
                  :placeholder="mcpPlaceholder(field)"
                  :disabled="Boolean(mcpEditing) && field.key === 'name'"
                />
                <span class="caption dim">{{ field.help }}</span>
              </label>
              <p class="caption dim">
                The server is contacted before it is saved, because one target that cannot
                answer takes the whole endpoint down with it.
              </p>
              <div class="row integration-actions">
                <button class="btn btn--sm" @click="cancelMcpAdd">Cancel</button>
                <button
                  class="btn btn--sm btn--primary"
                  :disabled="Boolean(mcpBusy) || !mcpDraftValid" @click="saveMcpServer"
                >
                  {{ mcpBusy ? 'Checking…' : mcpEditing ? 'Save server' : 'Add server' }}
                </button>
              </div>
            </div>

            <p v-if="!mcp.servers.length && !mcpLoading" class="caption dim integration-empty">
              No MCP server is configured.
            </p>
            <div v-for="server in mcp.servers" :key="server.name" class="integration-card">
              <div class="row">
                <span class="material-icons integration-card__icon">handyman</span>
                <strong class="grow">{{ server.name }}</strong>
                <span class="chip" :class="server.tool_count ? 'chip--success' : 'chip--warning'">
                  {{ server.tool_count }} tools
                </span>
              </div>
              <div class="integration-meta caption dim">
                <span class="mono truncate">{{ server.url }}</span>
                <span>{{ credentialLabel(server.credential) }}</span>
                <span v-if="!server.managed">from the gateway config file</span>
              </div>
              <div v-if="mcpTests[server.name]" class="line">
                <span class="dot" :class="mcpTests[server.name].ok ? 'dot--ok' : 'dot--bad'" />
                <span class="caption grow">{{ mcpTests[server.name].message }}</span>
                <span v-if="mcpTests[server.name].status" class="caption dim">
                  HTTP {{ mcpTests[server.name].status }}
                </span>
              </div>
              <div v-if="server.managed" class="row integration-actions">
                <button class="btn btn--sm" :disabled="Boolean(mcpBusy)" @click="editMcpServer(server)">
                  Configure
                </button>
                <button
                  class="btn btn--sm btn--outline" :disabled="Boolean(mcpBusy)"
                  @click="testMcpServer(server)"
                >
                  {{ mcpBusy === server.name ? 'Testing…' : 'Test' }}
                </button>
                <span class="grow" />
                <button
                  class="btn btn--sm btn--danger" :disabled="Boolean(mcpBusy)"
                  @click="removeMcpServer(server)"
                >
                  Remove
                </button>
              </div>
            </div>
          </div>

          <div class="setting">
            <div class="setting__label">Tools on offer</div>
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
const integrations = reactive({ integrations: [], available: [], storage_mode: 'unknown', writable: false })
const integrationLoading = ref(false)
const integrationAdding = ref(false)
const integrationChoice = ref('')
const integrationDraft = reactive({})
const integrationBusy = ref('')
const integrationAction = ref('')
const integrationTests = reactive({})
const mcp = reactive({ servers: [], fields: [], storage_mode: 'unknown', writable: false })
const mcpLoading = ref(false)
const mcpAdding = ref(false)
const mcpEditing = ref('')
const mcpDraft = reactive({})
const mcpBusy = ref('')
const mcpTests = reactive({})
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

const open = computed({
  get: () => store.settingsOpen,
  set: (value) => { store.settingsOpen = value }
})

const configuredIntegrations = computed(() => integrations.integrations)

const availableIntegrations = computed(() => integrations.available)

const selectedIntegration = computed(() =>
  [...integrations.integrations, ...integrations.available]
    .find((integration) => (integration.instance || integration.type) === integrationChoice.value))

const integrationDraftValid = computed(() =>
  (selectedIntegration.value?.fields || []).every((field) => {
    const value = (integrationDraft[field.key] || '').trim()
    if (!value) return Boolean(field.optional)
    return new RegExp(`^(?:${field.pattern})$`).test(value)
  }))

const mcpDraftValid = computed(() =>
  mcp.fields.every((field) => {
    const value = (mcpDraft[field.key] || '').trim()
    if (!value) return Boolean(field.optional)
    return new RegExp(`^(?:${field.pattern})$`).test(value)
  }))

function credentialLabel (credential) {
  if (credential.mode === 'environment') return `key from $${credential.variable}`
  if (credential.mode === 'stored') return 'key stored in agentgateway'
  if (credential.mode === 'gateway') return 'key held by agentgateway'
  return 'no key'
}

/** A stored key is never sent back here, so an empty field means "keep it". */
function secretPlaceholder (field) {
  if (field.kind !== 'secret') return ''
  const credential = selectedIntegration.value?.credential
  if (credential?.mode === 'stored') return 'stored — type a new key to replace'
  if (credential?.mode === 'environment') return `$${credential.variable}`
  if (credential?.mode === 'gateway') return credential.variable ? `$${credential.variable}` : ''
  return field.placeholder || ''
}

function mcpPlaceholder (field) {
  if (field.kind !== 'secret') return field.placeholder || ''
  const credential = mcp.servers.find((item) => item.name === mcpEditing.value)?.credential
  if (credential?.mode === 'stored') return 'stored — type a new token to replace'
  if (credential?.mode === 'environment') return `$${credential.variable}`
  return field.placeholder || ''
}

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

function applyIntegrations (data) {
  integrations.integrations = data.integrations || []
  integrations.available = data.available || []
  integrations.storage_mode = data.storage_mode || 'unknown'
  integrations.writable = Boolean(data.writable)
}

async function loadIntegrations () {
  integrationLoading.value = true
  try {
    applyIntegrations(await api.modelIntegrations())
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    integrationLoading.value = false
  }
}

async function refreshIntegrations () {
  await Promise.all([loadIntegrations(), actions.loadCatalog(true)])
}

function chooseIntegration (integration) {
  integrationAdding.value = true
  integrationChoice.value = integration.instance || integration.type
  for (const key of Object.keys(integrationDraft)) delete integrationDraft[key]
  for (const field of integration.fields || []) {
    if (field.kind === 'secret') integrationDraft[field.key] = ''
    else integrationDraft[field.key] = integration.config?.[field.key] || field.default || ''
  }
}

function clearIntegrationDraft () {
  integrationChoice.value = ''
  for (const key of Object.keys(integrationDraft)) delete integrationDraft[key]
}

function backFromIntegrationForm () {
  if (selectedIntegration.value?.configured) cancelIntegrationAdd()
  else clearIntegrationDraft()
}

function cancelIntegrationAdd () {
  integrationAdding.value = false
  clearIntegrationDraft()
}

function toggleIntegrationAdd () {
  if (integrationAdding.value) cancelIntegrationAdd()
  else {
    clearIntegrationDraft()
    integrationAdding.value = true
  }
}

async function addIntegration () {
  const integration = selectedIntegration.value
  if (!integration) return
  const target = integration.instance || integration.type
  integrationBusy.value = target
  integrationAction.value = 'save'
  try {
    const data = await api.addModelIntegration(target, { ...integrationDraft })
    applyIntegrations(data)
    cancelIntegrationAdd()
    await actions.loadCatalog(true)
    $q.notify({
      type: 'positive',
      message: `${integration.name} ${integration.configured ? 'updated' : 'added'}`
    })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    integrationBusy.value = ''
    integrationAction.value = ''
  }
}

function removeIntegration (integration) {
  $q.dialog({
    title: `Remove ${integration.name}`,
    message: 'Its models will disappear from the pickers. Existing chats keep their selected model.',
    cancel: true
  }).onOk(async () => {
    integrationBusy.value = integration.instance
    integrationAction.value = 'remove'
    try {
      const data = await api.removeModelIntegration(integration.instance)
      applyIntegrations(data)
      delete integrationTests[integration.instance]
      if (data.default_reset) await loadSettings()
      await actions.loadCatalog(true)
      $q.notify({ type: 'positive', message: `${integration.name} removed` })
    } catch (error) {
      $q.notify({ type: 'negative', message: error.message })
    } finally {
      integrationBusy.value = ''
      integrationAction.value = ''
    }
  })
}

async function testIntegration (integration) {
  integrationBusy.value = integration.instance
  integrationAction.value = 'test'
  delete integrationTests[integration.instance]
  try {
    const result = await api.testModelIntegration(integration.instance)
    integrationTests[integration.instance] = result
    if (result.ok) await Promise.all([loadIntegrations(), actions.loadCatalog(true)])
  } catch (error) {
    integrationTests[integration.instance] = { ok: false, message: error.message }
  } finally {
    integrationBusy.value = ''
    integrationAction.value = ''
  }
}

async function loadMcp () {
  mcpLoading.value = true
  try {
    Object.assign(mcp, await api.mcpServers())
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    mcpLoading.value = false
  }
}

async function refreshMcp () {
  refreshing.value = true
  await Promise.all([loadMcp(), actions.loadCatalog(true)])
  refreshing.value = false
}

function openMcpForm (server) {
  mcpAdding.value = true
  mcpEditing.value = server?.name || ''
  for (const key of Object.keys(mcpDraft)) delete mcpDraft[key]
  for (const field of mcp.fields) {
    mcpDraft[field.key] = field.kind === 'secret' ? '' : server?.[field.key] || ''
  }
}

const startMcpAdd = () => openMcpForm(null)
const editMcpServer = (server) => openMcpForm(server)

function cancelMcpAdd () {
  mcpAdding.value = false
  mcpEditing.value = ''
}

async function saveMcpServer () {
  const name = mcpDraft.name
  mcpBusy.value = name
  try {
    Object.assign(mcp, await api.saveMcpServer(name, { ...mcpDraft }))
    cancelMcpAdd()
    await actions.loadCatalog(true)
    $q.notify({ type: 'positive', message: `${name} ${mcpEditing.value ? 'updated' : 'added'}` })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    mcpBusy.value = ''
  }
}

function removeMcpServer (server) {
  $q.dialog({
    title: `Remove ${server.name}`,
    message: 'Its tools disappear from the pickers. Chats pinned to them keep the rest.',
    cancel: true
  }).onOk(async () => {
    mcpBusy.value = server.name
    try {
      Object.assign(mcp, await api.removeMcpServer(server.name))
      delete mcpTests[server.name]
      await actions.loadCatalog(true)
      $q.notify({ type: 'positive', message: `${server.name} removed` })
    } catch (error) {
      $q.notify({ type: 'negative', message: error.message })
    } finally {
      mcpBusy.value = ''
    }
  })
}

async function testMcpServer (server) {
  mcpBusy.value = server.name
  delete mcpTests[server.name]
  try {
    mcpTests[server.name] = await api.testMcpServer(server.name)
  } catch (error) {
    mcpTests[server.name] = { ok: false, message: error.message }
  } finally {
    mcpBusy.value = ''
  }
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

function loadTab (value) {
  if (value === 'agents') loadIntegrations()
  if (value === 'mcp') loadMcp()
  if (value === 'activity') actions.loadEvents()
}

watch(() => store.settingsTab, (value) => { tab.value = value })
watch(tab, loadTab)
watch(() => store.settingsOpen, (value) => {
  if (!value) return
  loadSettings()
  loadTab(tab.value)
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
  max-height: min(88dvh, calc(var(--app-height) - 48px));
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

.integration-add,
.integration-card {
  margin-top: 10px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-panel);
}

.integration-option {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 10px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.integration-option:hover {
  background: var(--surface-hover);
}

.integration-option .material-icons,
.integration-card__icon {
  flex: none;
  font-size: 18px;
  color: var(--accent);
}

.integration-option .grow,
.integration-field {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.integration-field {
  margin-top: 10px;
}

.integration-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 6px;
}

.integration-warning {
  margin-top: 6px !important;
  color: var(--warning);
}

.integration-empty {
  margin-top: 12px !important;
}

.integration-actions {
  margin-top: 10px;
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
    height: calc(var(--app-height) - max(24px, env(safe-area-inset-top)) - max(24px, env(safe-area-inset-bottom)));
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
  .settings__save,
  .integration-actions {
    flex-wrap: wrap;
  }

  .integration-head .setting__label {
    flex-basis: 100%;
  }

  .integration-head .btn {
    flex: 1;
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
