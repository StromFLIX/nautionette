<template>
  <q-dialog
    :model-value="visible"
    :persistent="busy"
    aria-labelledby="mcp-login-title"
    aria-describedby="mcp-login-description"
    @hide="close"
  >
    <q-card class="mcp-login-dialog">
      <header class="mcp-login-header">
        <span class="mcp-login-symbol" aria-hidden="true"><span class="material-icons">key</span></span>
        <h2 id="mcp-login-title">Sign in to <span class="mcp-login-name">{{ selected?.name }}</span></h2>
        <button v-if="!busy" class="btn btn--icon mcp-login-close" aria-label="Dismiss login reminder" @click="close">
          <span class="material-icons" aria-hidden="true">close</span>
        </button>
      </header>
      <p id="mcp-login-description" class="mcp-login-description">Reconnect with your saved settings.</p>
      <p v-if="!supported()" class="mcp-login-notice caption" role="alert">{{ oauthClientMessage }}</p>
      <p v-if="stage && busy" class="mcp-login-status caption" role="status">
        <span class="material-icons" aria-hidden="true">hourglass_top</span>
        <span>{{ progressMessage }}</span>
      </p>
      <p v-if="error" class="mcp-login-error caption" role="alert">{{ error }}</p>
      <section v-if="!busy" class="mcp-login-reminder" aria-label="Login reminder options">
        <div class="mcp-login-reminder-control">
          <label for="mcp-login-snooze">Remind me in</label>
          <div class="mcp-login-select">
            <select
              id="mcp-login-snooze"
              v-model.number="snoozeFor"
              class="field"
              aria-label="Snooze login reminders on this device"
              aria-describedby="mcp-login-snooze-hint"
            >
              <option :value="3600000">1 hour</option>
              <option :value="28800000">8 hours</option>
              <option :value="86400000">24 hours</option>
              <option :value="604800000">7 days</option>
            </select>
            <span class="material-icons" aria-hidden="true">expand_more</span>
          </div>
        </div>
        <p id="mcp-login-snooze-hint" class="caption">Only this device, for this instance.</p>
      </section>
      <footer class="mcp-login-actions">
        <button
          class="btn"
          :class="!busy && !supported() ? 'btn--primary' : 'btn--outline'"
          :autofocus="!supported()"
          @click="busy ? close() : snooze()"
        >{{ busy ? 'Cancel' : 'Snooze' }}</button>
        <button class="btn" :class="supported() ? 'btn--primary' : 'btn--outline'" :autofocus="supported()" :disabled="busy || !supported()" @click="login">
          {{ busy ? 'Signing in…' : error ? 'Try again' : 'Login' }}
        </button>
      </footer>
      <button v-if="error" class="btn btn--sm mcp-login-settings" @click="settings">Connection settings</button>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { api, auth, server } from '../api'
import { actions, store } from '../store'
import { desktopOAuth, oauthClientMessage, oauthConnection } from '../mcp-oauth.js'

const $q = useQuasar()
const selected = computed(() => store.mcpAuth.prompt)
const visible = computed(() => Boolean(selected.value) && !store.needsServer && !store.needsToken)
const supported = () => Boolean(desktopOAuth())
const busy = ref(false),
  stage = ref(''),
  error = ref(''),
  snoozeFor = ref(supported() ? 3600000 : 86400000)
const progressMessage = computed(() => {
  const value = stage.value
  if (value.startsWith('Sign in in your browser')) return 'Complete sign-in in your browser.'
  if (value.startsWith('Exchanging the code') || value.startsWith('Forwarding callback')) return 'Completing sign-in and reconnecting…'
  if (value === 'Opening the local OAuth callback port…') return 'Preparing secure sign-in…'
  if (value === 'Starting hosted HTTPS OAuth…' || value === 'Starting CLI login inside agentgateway…') return 'Starting sign-in…'
  if (value === 'Waiting for CLI…') return 'Waiting for sign-in…'
  return value
})
watch(selected, value => {
  if (value && !busy.value) snoozeFor.value = supported() ? 3600000 : 86400000
})
let controller = null,
  attempt = 0,
  activeName = ''
const context = () => `${server.url}|${auth.token}`

async function login() {
  const name = selected.value.name,
    id = ++attempt,
    initial = context()
  busy.value = true
  activeName = name
  error.value = ''
  actions.beginMcpLogin(name)
  try {
    stage.value = 'Preparing your saved connection…'
    const config = await api.prepareMcpOAuth(name)
    if (id !== attempt || initial !== context()) return
    controller = oauthConnection({ backend: api, context })
    await controller.connect(name, config, (value) => {
      if (id === attempt) stage.value = value
    })
    if (id !== attempt) return
    await Promise.all([actions.loadMcpAuth(), actions.loadCatalog(true)])
    actions.dismissMcpLogin()
    $q.notify({ type: 'positive', message: `${name} connected` })
  } catch (failure) {
    if (id === attempt) error.value = failure.message
  } finally {
    if (id === attempt) {
      busy.value = false
      controller = null
      activeName = ''
      actions.finishMcpLogin(name)
    }
  }
}

function close() {
  const name = selected.value?.name || activeName
  activeName = ''
  attempt++
  controller?.cancel()
  controller = null
  busy.value = false
  stage.value = error.value = ''
  actions.dismissMcpLogin()
  if (name) actions.finishMcpLogin(name)
}
function snooze() {
  if (actions.snoozeMcpLogin(snoozeFor.value)) close()
}
function settings() {
  close()
  actions.openSettings('mcp')
}
onBeforeUnmount(close)
</script>

<style scoped>
.mcp-login-dialog {
  width: min(440px, calc(100vw - 32px));
  padding: var(--space-5);
  overflow-wrap: anywhere;
}
.mcp-login-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.mcp-login-symbol {
  display: grid;
  place-items: center;
  flex: none;
  width: 2.25rem;
  height: 2.25rem;
  border-radius: var(--radius-md);
  background: var(--accent-soft);
  color: var(--accent);
}
.mcp-login-symbol .material-icons { font-size: 1.25rem; }
.mcp-login-dialog h2 {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 1.125rem;
  font-weight: 600;
  line-height: 1.35;
}
.mcp-login-close {
  align-self: flex-start;
  width: max(44px, 2.125rem);
  min-height: max(44px, 2.125rem);
  color: var(--text-dim);
}
.mcp-login-description {
  margin: var(--space-4) 0 0;
  color: var(--text-muted);
  font-size: 0.8125rem;
  line-height: 1.6;
}
.mcp-login-notice,
.mcp-login-status,
.mcp-login-error {
  margin: var(--space-4) 0 0;
  padding: var(--space-3);
  border-radius: var(--radius-sm);
}
.mcp-login-notice {
  background: var(--surface-raised);
  color: var(--text-muted);
}
.mcp-login-status {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  background: var(--accent-soft);
  color: var(--text);
}
.mcp-login-status .material-icons { flex: none; color: var(--accent); }
.mcp-login-error {
  background: var(--danger-soft);
  color: var(--danger);
}
.mcp-login-reminder {
  margin-top: var(--space-5);
  padding: var(--space-4);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--surface-input);
}
.mcp-login-reminder-control {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 7.5rem), 1fr));
  align-items: center;
  gap: var(--space-3);
}
.mcp-login-reminder-control label { font-size: 0.8125rem; font-weight: 500; }
.mcp-login-select { position: relative; min-width: 0; }
.mcp-login-select select {
  display: block;
  appearance: none;
  min-height: 44px;
  padding: 0.55rem 2rem 0.55rem var(--space-3);
  border-color: var(--border-strong);
  background: var(--surface-overlay);
  line-height: 1.4;
  cursor: pointer;
}
.mcp-login-select .material-icons {
  position: absolute;
  top: 50%;
  right: var(--space-2);
  transform: translateY(-50%);
  color: var(--text-muted);
  pointer-events: none;
}
.mcp-login-reminder p { margin: var(--space-3) 0 0; color: var(--text-muted); }
.mcp-login-actions {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 6.25rem), 1fr));
  gap: var(--space-3);
  margin-top: var(--space-5);
}
.mcp-login-actions .btn { min-height: 44px; white-space: nowrap; overflow-wrap: normal; }
.mcp-login-settings { width: 100%; margin-top: var(--space-2); color: var(--text-muted); }
@media (max-width: 380px) {
  .mcp-login-symbol { display: none; }
}
</style>
