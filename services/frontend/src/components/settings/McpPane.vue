<template>
  <h2 class="settings__title">MCP servers</h2>

  <div id="mcp-servers" class="setting">
    <div class="row integration-head">
      <div class="setting__label grow">Servers</div>
      <button class="btn btn--sm btn--outline" :disabled="loading" @click="refresh">
        {{ loading ? 'Checking…' : 'Refresh' }}
      </button>
      <button
        class="btn btn--sm btn--primary" :disabled="!state.writable || adding" @click="startAdd"
      >
        Add server
      </button>
    </div>
    <p class="caption dim">
      Connected tools are available in every chat.
    </p>

    <div v-if="adding" class="integration-add">
      <strong>{{ editing ? `Configure ${editing}` : 'Add a server' }}</strong>
      <label class="integration-field">
        <span class="caption">Transport</span>
        <select v-model="transport" class="field" aria-label="Transport" :disabled="Boolean(busy)">
          <option value="http">HTTP — connect to a server</option>
          <option value="stdio">stdio — run inside agentgateway</option>
        </select>
      </label>
      <label class="integration-field">
        <span class="caption">Authentication</span>
        <select v-model="authentication" class="field" aria-label="Authentication" :disabled="Boolean(busy)">
          <option value="token">{{ transport === 'http' ? 'Access token / none' : 'Environment credentials / none' }}</option>
          <option value="oauth">{{ transport === 'http' ? 'HTTPS OAuth — sign in with desktop app' : 'CLI OAuth — sign in with desktop app' }}</option>
        </select>
      </label>
      <DeclaredField
        v-for="field in activeFields" :key="field.key" v-model="draft[field.key]"
        :field="field" :credential="editingCredential"
        :disabled="Boolean(busy) || (Boolean(editing) && field.key === 'name')"
      />
      <template v-if="transport === 'http' && authentication === 'oauth'">
        <p v-if="!oauthSupported" class="caption field-hint--bad" role="alert">{{ oauthClientMessage }}</p>
        <p v-else-if="!oauthCapabilities.http?.available" class="caption field-hint--bad" role="alert">Deploy the matching backend with authentication to enable HTTPS OAuth.</p>
        <div class="row integration-actions">
          <button class="btn btn--sm btn--outline" :disabled="Boolean(busy)" @click="hostedWorkiqPreset">Use WorkIQ HTTPS</button>
          <button class="btn btn--sm btn--outline" :disabled="Boolean(busy) || !draft.url" @click="discoverHosted">Discover OAuth</button>
        </div>
        <p class="caption dim">Connect directly to the hosted MCP endpoint. No WorkIQ CLI, npx process or container browser is used.</p>
        <details class="integration-details">
          <summary>OAuth client settings</summary>
          <label class="integration-field"><span class="caption">Public OAuth client ID</span>
            <input v-model="httpConfig.client_id" class="field mono" aria-label="Public OAuth client ID" :disabled="Boolean(busy)" />
          </label>
          <label class="integration-field"><span class="caption">Loopback redirect URI</span>
            <input v-model="httpConfig.redirect_uri" class="field mono" aria-label="Loopback redirect URI" :disabled="Boolean(busy)" />
            <span class="caption dim">Use a redirect registered for this public client. Port 0 selects a temporary port only if the provider permits it.</span>
          </label>
          <label class="integration-field"><span class="caption">OAuth scopes</span>
            <input v-model="httpConfig.scope" class="field mono" aria-label="OAuth scopes" :disabled="Boolean(busy)" />
          </label>
          <label class="integration-field"><span class="caption">Callback response mode</span>
            <select v-model="httpConfig.response_mode" class="field" aria-label="Callback response mode" :disabled="Boolean(busy)">
              <option value="query">Query redirect (GET)</option><option value="form_post">Form POST</option>
            </select>
          </label>
        </details>
        <p v-if="httpProfile" class="caption dim">Issuer: {{ httpProfile.issuer }}. Registration/redirect permissions are checked by the provider during login, not discoverable from metadata.</p>
        <label class="integration-field row">
          <input v-model="shareAccount" type="checkbox" aria-label="Share OAuth account with this instance" :disabled="Boolean(busy)" />
          <span class="caption">I authorize this shared instance to access my account. Connected tools are available to all chats and users of this instance.</span>
        </label>
        <p class="caption dim">The desktop only receives the local callback. PKCE and token exchange stay in the backend; credentials are encrypted at rest and refreshed server-side. No tokens or codes need copying.</p>
        <p v-if="oauthStage" class="caption" role="status">{{ oauthStage }}</p>
      </template>
      <template v-if="transport === 'stdio' && authentication === 'oauth'">
        <p v-if="!oauthSupported" class="caption field-hint--bad" role="alert">{{ oauthClientMessage }} <a href="https://github.com/StromFLIX/nautionette/releases/latest" target="_blank" rel="noopener">Get the desktop app</a>.</p>
        <p v-else-if="!oauthCapabilities.available" class="caption field-hint--bad" role="alert">{{ oauthCapabilities.message }}</p>
        <p class="caption">This grants this instance access to your account. Tools and the CLI's stored account are shared across all chats and users of this instance. Only connect an account you intend to share here.</p>
        <label class="integration-field">
          <span class="caption">CLI adapter</span>
          <select v-model="oauthProvider" class="field" aria-label="CLI adapter" :disabled="Boolean(busy)">
            <option value="generic">Generic loopback OAuth CLI</option>
            <option value="workiq">WorkIQ 1.0.1</option>
          </select>
        </label>
        <button class="btn btn--sm btn--outline" :disabled="Boolean(busy)" @click="workiqPreset">Use WorkIQ preset</button>
        <template v-if="oauthProvider === 'generic'">
          <label class="integration-field">
            <span class="caption">Login arguments (JSON array)</span>
            <textarea v-model="loginArgsText" class="field mono" rows="2" aria-label="Login arguments" :disabled="Boolean(busy)" placeholder='["auth", "login"]' />
          </label>
          <label class="integration-field">
            <span class="caption">Setup commands (JSON array of argument arrays)</span>
            <textarea v-model="setupArgsText" class="field mono" rows="2" aria-label="Setup commands" :disabled="Boolean(busy)" placeholder="[]" />
          </label>
          <p class="caption dim">Login/setup use the executable below, without a shell. Arguments are visible configuration; use environment variables for secrets. The CLI must expose a code/PKCE login URL through BROWSER, xdg-open or its output. Native-broker/device-code-only flows are not supported.</p>
        </template>
        <template v-else>
          <p class="caption dim">The preset pins WorkIQ, selects its supported browser authentication and English status messages, and runs its login command in agentgateway. Tenant consent, licensing and policy still apply.</p>
          <label class="integration-field row">
            <input v-model="acceptEula" type="checkbox" aria-label="Accept WorkIQ EULA" :disabled="Boolean(busy)" />
            <span class="caption">I accept the <a :href="oauthCapabilities.workiq?.eula_url || 'https://www.npmjs.com/package/@microsoft/workiq/v/1.0.1?activeTab=code'" target="_blank" rel="noopener">WorkIQ EULA</a> and authorize connecting this account to this shared instance.</span>
          </label>
        </template>
        <p class="caption dim">The callback port is detected from the CLI's actual login URL and opened temporarily on your desktop. No firewall changes are needed. The CLI, not Nautionette, owns token storage and refresh.</p>
        <p v-if="oauthStage" class="caption" role="status">{{ oauthStage }}</p>
      </template>
      <template v-if="transport === 'stdio'">
        <label class="integration-field">
          <span class="caption">Arguments (JSON array)</span>
          <textarea v-model="argsText" class="field mono" rows="3" spellcheck="false"
            placeholder='["-y", "@brave/brave-search-mcp-server", "--transport", "stdio"]' />
          <span v-if="parsedArgs === null" class="caption field-hint--bad" role="alert">Enter a JSON array of strings (up to 256 arguments, no NUL characters).</span>
          <span class="caption dim">Passed as separate arguments, without a shell. Use environment variables for secrets.</span>
        </label>
        <div class="integration-field">
          <span class="caption">Environment variables</span>
          <div v-for="(variable, index) in environment" :key="variable.id" class="mcp-env-row">
            <input v-model="variable.key" class="field mono" :aria-label="`Variable ${index + 1} name`"
              placeholder="BRAVE_API_KEY" :disabled="variable.stored" autocomplete="off" />
            <input v-model="variable.value" class="field mono" type="password" :aria-label="`Variable ${index + 1} value`"
              :placeholder="variable.stored ? 'stored — leave blank to keep' : 'Value (literal)'" autocomplete="new-password" />
            <button class="btn btn--sm" :aria-label="`Remove variable ${index + 1}`" @click="environment.splice(index, 1)">Remove</button>
          </div>
          <button class="btn btn--sm btn--outline" @click="addVariable">Add variable</button>
          <span v-if="environmentError" class="caption field-hint--bad" role="alert">{{ environmentError }}</span>
          <span class="caption dim">Values are stored in agentgateway and never returned. Remove a row to delete it. Values are literal; $VARIABLE is not expanded.</span>
        </div>
        <p class="caption">
          Only run trusted packages: these processes share agentgateway's container, network and data volume.
          Node/npm/npx and Python/uvx are available. Other runtimes must be installed in the image.
          Use stdio mode, not an HTTP listener, and pin package versions.
        </p>
      </template>
      <p class="caption dim">
        {{ transport === 'stdio'
          ? 'The process is tested on an isolated gateway route before saving. First-time package downloads can take up to 90 seconds.'
          : 'Connection is checked before saving to protect the shared tool endpoint.' }}
      </p>
      <div class="row integration-actions">
        <button class="btn btn--sm" @click="cancelAdd">Cancel</button>
        <button
          class="btn btn--sm btn--primary" :disabled="Boolean(busy) || !draftValid" @click="save"
        >
          {{ busy ? 'Checking…' : authentication === 'oauth' ? transport === 'http' ? 'Connect with HTTPS OAuth' : 'Connect with CLI OAuth' : editing ? 'Save server' : 'Add server' }}
        </button>
      </div>
    </div>

    <p v-if="!state.servers.length && !loading" class="caption dim integration-empty">
      No MCP server is configured.
    </p>
    <div v-for="server in state.servers" :key="server.name" class="integration-card">
      <div class="row">
        <span class="material-icons integration-card__icon">handyman</span>
        <strong class="grow">{{ server.name }}</strong>
        <span class="chip" :class="server.tool_count ? 'chip--success' : 'chip--warning'">
          {{ server.tool_count }} tools
        </span>
      </div>
      <details class="integration-details">
        <summary>Connection details</summary>
        <div class="integration-meta caption dim">
          <span class="caption">{{ server.transport === 'stdio' ? 'stdio · runs in agentgateway' : 'HTTP' }}</span>
          <template v-if="server.transport === 'stdio'">
            <span class="mono mcp-command">{{ server.command }} {{ JSON.stringify(server.args || []) }}</span>
            <span>Stored environment: {{ Object.keys(server.env || {}).join(', ') || 'none' }}</span>
            <span v-if="server.oauth">CLI OAuth · {{ server.oauth.provider }} · credentials and refresh managed by the CLI</span>
          </template>
          <template v-else>
            <span class="mono truncate">{{ server.url }}</span>
            <span>{{ credentialLabel(server.credential) }}</span>
            <span v-if="server.oauth?.mode === 'https'">HTTPS OAuth · {{ server.oauth.status }} · {{ server.oauth.refreshable ? 'backend refresh' : 'sign in again after expiry' }}</span>
          </template>
          <span v-if="!server.managed">from the gateway config file</span>
        </div>
      </details>
      <div v-if="tests[server.name]" class="line">
        <span class="dot" :class="tests[server.name].ok ? 'dot--ok' : 'dot--bad'" />
        <span class="caption grow">{{ tests[server.name].message }}</span>
        <span v-if="tests[server.name].status" class="caption dim">
          HTTP {{ tests[server.name].status }}
        </span>
      </div>
      <div v-if="server.managed" class="row integration-actions">
        <button class="btn btn--sm" :disabled="Boolean(busy)" @click="edit(server)">
          Configure
        </button>
        <button class="btn btn--sm btn--outline" :disabled="Boolean(busy)" @click="test(server)">
          {{ busy === server.name ? 'Testing…' : 'Test' }}
        </button>
        <span class="grow" />
        <button class="btn btn--sm btn--danger" :disabled="Boolean(busy)" @click="remove(server)">
          Remove
        </button>
      </div>
    </div>
    <div v-for="item in orphanedOAuth" :key="item.name" class="integration-card">
      <strong>{{ item.name }}</strong>
      <p class="caption">HTTPS OAuth · {{ item.status }}. {{ item.status === 'storage_unavailable' ? 'Restore the backend credential-key backup.' : 'Sign in again to reconnect.' }}</p>
      <div class="row integration-actions">
        <button class="btn btn--sm" :disabled="Boolean(busy) || !state.writable" @click="openForm({ ...item, transport: 'http', oauth: item })">Reconnect</button>
        <button class="btn btn--sm btn--danger" :disabled="Boolean(busy) || !state.writable" @click="remove(item)">Remove saved connection</button>
      </div>
    </div>
  </div>

  <div id="tool-catalog" class="setting">
    <div class="setting__label">Tool catalog</div>
    <input v-model="toolQuery" class="field" type="search" placeholder="Search tools…" aria-label="Search tool catalog" />
    <p v-if="!toolGroups.length" class="caption dim integration-empty">{{ toolQuery ? 'No matching tools.' : 'No MCP server answered.' }}</p>
    <details v-for="group in toolGroups" :key="group.name" class="settings-disclosure model-catalog" :open="Boolean(toolQuery)">
      <summary>
        <span class="grow truncate">{{ group.name }}</span>
        <span v-if="group.host" class="caption dim mono truncate">{{ group.host }}</span>
        <span class="chip chip--success">{{ group.tools.length }} tools</span>
      </summary>
      <div v-for="tool in group.tools" :key="tool.name" class="line line--stacked line--nested">
        <span class="mono">{{ tool.name }}</span>
        <span v-if="tool.description" class="caption dim">{{ tool.description }}</span>
      </div>
    </details>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import DeclaredField from './DeclaredField.vue'
import { credentialLabel, draftIsValid, resetDraft } from './fields'
import { actions, store } from '../../store'
import { api, auth, server as instanceServer } from '../../api'
import { desktopOAuth, oauthClientMessage, oauthConnection } from '../../mcp-oauth.js'

const $q = useQuasar()

const state = reactive({ servers: [], fields: [], stdio_fields: [], storage_mode: 'unknown', writable: false })
const tests = reactive({})
const draft = reactive({})
const loading = ref(false)
const adding = ref(false)
const editing = ref('')
const busy = ref('')
const toolQuery = ref('')

const transport = ref('http')
const authentication = ref('token')
const oauthSupported = Boolean(desktopOAuth())
const oauthProvider = ref('generic')
const oauthCapabilities = reactive({ available: false, http: { available: false }, message: 'Checking OAuth support…' })
const httpConfig = reactive({ client_id: '', redirect_uri: 'http://127.0.0.1:0/callback', scope: '', response_mode: 'query' })
const httpProfile = ref(null)
const shareAccount = ref(false)
const orphanedOAuth = computed(() => Object.entries(state.oauth_connections || {})
  .filter(([name, item]) => item.mode === 'https' && !state.servers.some(server => server.name === name))
  .map(([name, item]) => ({ name, ...item })))
const loginArgsText = ref('[]')
const setupArgsText = ref('[]')
const acceptEula = ref(false)
const oauthStage = ref('')
let oauthController = null
const argsText = ref('[]')
const environment = ref([])
let nextVariableId = 0
const activeFields = computed(() => transport.value === 'stdio' ? state.stdio_fields : state.fields.filter(field => authentication.value !== 'oauth' || field.key !== 'token'))
const parsedArgs = computed(() => {
  try {
    const args = JSON.parse(argsText.value)
    return Array.isArray(args) && args.length <= 256 && args.every(arg =>
      typeof arg === 'string' && !arg.includes('\u0000') && arg.length <= 8192) ? args : null
  } catch { return null }
})
const environmentError = computed(() => {
  const keys = environment.value.map(variable => variable.key)
  if (new Set([...keys, 'HOME', 'PATH']).size > 128) return 'Use at most 128 variables, including HOME and PATH.'
  if (keys.some(key => !/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(key))) return 'Variable names must start with a letter or underscore and contain only letters, digits and underscores.'
  if (new Set(keys).size !== keys.length) return 'Each variable name must be unique.'
  if (environment.value.some(variable => variable.value.includes('\u0000') || variable.value.length > 32768)) return 'Variable values must be at most 32768 characters and contain no NUL characters.'
  return ''
})
function parseArguments (text) {
  try {
    const value = JSON.parse(text)
    return Array.isArray(value) && value.length <= 256 && value.every(v => typeof v === 'string' && v.length <= 8192 && !v.includes('\u0000')) ? value : null
  } catch { return null }
}
const parsedLoginArgs = computed(() => parseArguments(loginArgsText.value))
const parsedSetupArgs = computed(() => {
  try {
    const value = JSON.parse(setupArgsText.value)
    return Array.isArray(value) && value.length <= 4 && value.every(v => parseArguments(JSON.stringify(v)) !== null) ? value : null
  } catch { return null }
})
const draftValid = computed(() => draftIsValid(activeFields.value, draft) && (
  transport.value !== 'stdio' || (parsedArgs.value !== null && !environmentError.value)
) && (authentication.value !== 'oauth' || (oauthSupported && (
  transport.value === 'http'
    ? oauthCapabilities.http?.available && shareAccount.value && httpConfig.client_id && httpConfig.redirect_uri && httpProfile.value?.url === draft.url
    : oauthCapabilities.available && (oauthProvider.value === 'workiq' ? acceptEula.value : parsedLoginArgs.value !== null && parsedSetupArgs.value !== null)
))))

function addVariable () {
  environment.value.push({ id: nextVariableId++, key: '', value: '', stored: false })
}

const editingCredential = computed(() =>
  state.servers.find((item) => item.name === editing.value)?.credential || null)

const toolGroups = computed(() => {
  const query = toolQuery.value.trim().toLowerCase()
  return (store.catalog.tool_servers || []).map(server => ({
    ...server,
    tools: (store.catalog.tools || []).filter(tool => tool.server === server.name &&
      `${server.name} ${tool.name} ${tool.description || ''}`.toLowerCase().includes(query))
  })).filter(server => !query || server.tools.length)
})

async function load () {
  loading.value = true
  try {
    Object.assign(state, await api.mcpServers())
    Object.assign(oauthCapabilities, await api.mcpOAuthCapabilities().catch(() => ({ available: false, message: 'Deploy the matching gateway image to enable CLI OAuth.' })))
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    loading.value = false
  }
}

async function refresh () {
  loading.value = true
  await Promise.all([load(), actions.loadCatalog(true)])
  loading.value = false
}

function openForm (server) {
  adding.value = true
  editing.value = server?.name || ''
  transport.value = server?.transport || 'http'
  authentication.value = server?.oauth ? 'oauth' : 'token'
  oauthProvider.value = server?.oauth?.provider || 'generic'
  loginArgsText.value = JSON.stringify(server?.oauth?.login_args || [], null, 2)
  setupArgsText.value = JSON.stringify(server?.oauth?.setup_args || [], null, 2)
  acceptEula.value = false
  shareAccount.value = false
  httpProfile.value = null
  Object.assign(httpConfig, { client_id: server?.oauth?.client_id || '', redirect_uri: server?.oauth?.redirect_uri || 'http://127.0.0.1:0/callback',
    scope: server?.oauth?.scope || '', response_mode: server?.oauth?.response_mode || 'query' })
  oauthStage.value = ''
  resetDraft(draft, [...state.fields, ...state.stdio_fields], server || {})
  argsText.value = JSON.stringify(server?.args || [], null, 2)
  environment.value = Object.keys(server?.env || {}).map(key => ({
    id: nextVariableId++, key, value: '', stored: true
  }))
}

const startAdd = () => openForm(null)
const edit = (server) => openForm(server)

function cancelAdd () {
  oauthController?.cancel()
  oauthController = null
  oauthStage.value = ''
  adding.value = false
  editing.value = ''
  for (const key of Object.keys(draft)) delete draft[key]
  environment.value = []
  argsText.value = '[]'
}

function workiqPreset () {
  transport.value = 'stdio'
  authentication.value = 'oauth'
  oauthProvider.value = 'workiq'
  draft.command = 'npx'
  if (!draft.name) draft.name = 'workiq'
  argsText.value = JSON.stringify(oauthCapabilities.workiq?.args || ['-y', '@microsoft/workiq@1.0.1', 'mcp'])
  acceptEula.value = false
}

async function hostedWorkiqPreset () {
  transport.value = 'http'
  authentication.value = 'oauth'
  draft.url = oauthCapabilities.http?.workiq?.url || 'https://workiq.svc.cloud.microsoft/mcp'
  if (!draft.name) draft.name = 'workiq'
  Object.assign(httpConfig, { client_id: oauthCapabilities.http?.workiq?.client_id || 'ba081686-5d24-4bc6-a0d6-d034ecffed87',
    redirect_uri: oauthCapabilities.http?.workiq?.redirect_uri || 'http://localhost:12798', scope: '', response_mode: 'query' })
  await discoverHosted()
}

async function discoverHosted () {
  busy.value = 'discovery'
  httpProfile.value = null
  try {
    const profile = await api.discoverMcpOAuth({ url: draft.url, ...httpConfig })
    httpProfile.value = profile
    Object.assign(httpConfig, { client_id: profile.client_id, redirect_uri: profile.redirect_uri, scope: profile.scope, response_mode: profile.response_mode })
  } catch (error) { $q.notify({ type: 'negative', message: error.message }) } finally { busy.value = '' }
}

async function save () {
  const name = draft.name
  busy.value = name
  try {
    if (transport.value === 'http' && authentication.value === 'oauth') {
      oauthController = oauthConnection({ backend: api, context: () => `${instanceServer.url}|${auth.token}` })
      await oauthController.connect(name, { transport: 'http', url: draft.url, ...httpConfig,
        issuer: httpProfile.value.issuer, discovery_fingerprint: httpProfile.value.discovery_fingerprint, share_account: shareAccount.value },
      stage => { oauthStage.value = stage })
      oauthController = null
      await refresh()
      cancelAdd()
      $q.notify({ type: 'positive', message: `${name}: HTTPS OAuth connected and MCP checked` })
      return
    }
    const payload = transport.value === 'stdio'
      ? { transport: 'stdio', command: draft.command, args: parsedArgs.value,
          env: Object.fromEntries(environment.value.map(variable => [variable.key,
            variable.stored && variable.value === '' ? null : variable.value])) }
      : { transport: 'http', url: draft.url, token: draft.token }
    if (transport.value === 'stdio' && authentication.value === 'oauth') {
      oauthController = oauthConnection({ backend: api, context: () => `${instanceServer.url}|${auth.token}` })
      await oauthController.connect(name, { ...payload, provider: oauthProvider.value,
        login_args: parsedLoginArgs.value, setup_args: parsedSetupArgs.value, accept_eula: acceptEula.value },
      stage => { oauthStage.value = stage })
      oauthController = null
      await refresh()
      cancelAdd()
      $q.notify({ type: 'positive', message: `${name}: CLI login finished and MCP connection checked` })
      return
    }
    Object.assign(state, await api.saveMcpServer(name, payload))
    const verb = editing.value ? 'updated' : 'added'
    cancelAdd()
    await actions.loadCatalog(true)
    $q.notify({ type: 'positive', message: `${name} ${verb}` })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    busy.value = ''
  }
}

function remove (server) {
  $q.dialog({
    title: `Remove ${server.name}`,
    message: 'Its tools disappear from the pickers. Chats pinned to them keep the rest.' + (server.oauth?.mode === 'https' || server.mode === 'https' ? ' Local OAuth credentials and refresh will be removed; revoke provider consent separately if needed.' : server.oauth ? ' Removing a connection does not revoke provider consent or delete the CLI account cache.' : ''),
    cancel: true
  }).onOk(async () => {
    busy.value = server.name
    try {
      Object.assign(state, await api.removeMcpServer(server.name))
      delete tests[server.name]
      await actions.loadCatalog(true)
      $q.notify({ type: 'positive', message: `${server.name} removed` })
    } catch (error) {
      $q.notify({ type: 'negative', message: error.message })
    } finally {
      busy.value = ''
    }
  })
}

async function test (server) {
  busy.value = server.name
  delete tests[server.name]
  try {
    tests[server.name] = await api.testMcpServer(server.name)
  } catch (error) {
    tests[server.name] = { ok: false, message: error.message }
  } finally {
    busy.value = ''
  }
}

onMounted(load)
onBeforeUnmount(() => { oauthController?.cancel() })
</script>

<style scoped>
.mcp-env-row { display: flex; flex-wrap: wrap; gap: 8px; }
.mcp-env-row .field { flex: 1 1 150px; min-width: 0; }
.mcp-command { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
