<template>
  <q-dialog :model-value="visible" :persistent="busy" @hide="close">
    <q-card class="mcp-login-dialog">
      <h2>Sign in to {{ selected?.name }}</h2>
      <p class="caption dim">
        {{
          selected?.status === 'reauth_required'
            ? 'This connection needs you to sign in again.'
            : 'Sign in again using your saved connection.'
        }}
        No setup changes are needed.
      </p>
      <p v-if="!supported()" class="caption" role="alert">{{ oauthClientMessage }}</p>
      <p v-if="stage && busy" class="caption" role="status">{{ stage }}</p>
      <p v-if="error" class="caption mcp-login-error" role="alert">{{ error }}</p>
      <div class="row">
        <button class="btn btn--primary" :disabled="busy || !supported()" @click="login">
          {{ busy ? 'Signing in…' : error ? 'Try again' : 'Login' }}
        </button>
        <button class="btn" @click="close">{{ busy ? 'Cancel' : 'Later' }}</button>
        <button v-if="error" class="btn btn--outline" @click="settings">Connection settings</button>
      </div>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
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
  error = ref('')
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
function settings() {
  close()
  actions.openSettings('mcp')
}
onBeforeUnmount(close)
</script>

<style scoped>
.mcp-login-dialog {
  width: min(420px, calc(100vw - 32px));
  padding: 24px;
}
.mcp-login-dialog h2 {
  margin: 0 0 12px;
  font-size: 1.25rem;
}
.mcp-login-dialog .row {
  margin-top: 20px;
  flex-wrap: wrap;
  gap: 8px;
}
.mcp-login-error {
  color: var(--danger, #e57373);
}
</style>
