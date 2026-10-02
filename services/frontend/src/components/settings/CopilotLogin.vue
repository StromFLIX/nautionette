<template>
  <div class="setting">
    <p class="caption dim">
      Authorize with the GitHub account that has your Copilot subscription.
      GitHub may identify the authorization app as GitHub Copilot.
    </p>
    <div v-if="device" role="status" aria-live="polite">
      <p>Enter this one-time code on GitHub: <strong class="mono">{{ device.user_code }}</strong></p>
      <a class="btn btn--sm btn--primary" :href="device.verification_uri" target="_blank" rel="noopener noreferrer">
        Open GitHub to authorize
      </a>
      <p class="caption dim">Waiting for authorization. This code expires in {{ Math.ceil(device.expires_in / 60) }} minutes.</p>
    </div>
    <p v-if="error" class="caption integration-warning" role="alert">{{ error }}</p>
    <div class="row integration-actions">
      <button v-if="!device" class="btn btn--sm btn--primary" :disabled="disabled || working" @click="start">
        {{ working ? 'Starting sign-in…' : 'Sign in with GitHub' }}
      </button>
      <button v-if="device && error" class="btn btn--sm btn--primary" :disabled="polling" @click="poll">
        Retry connection
      </button>
      <button v-if="device" class="btn btn--sm" :disabled="cancelling" @click="cancel">
        {{ cancelling ? 'Cancelling…' : 'Cancel sign-in' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { useQuasar } from 'quasar'
import { api } from '../../api'

const props = defineProps({ config: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['busy', 'connected'])
const $q = useQuasar()
const device = ref(null)
const error = ref('')
const working = ref(false)
const polling = ref(false)
const cancelling = ref(false)
let timer
let expiresAt = 0
let revision = 0

function setBusy (value) {
  working.value = value
  emit('busy', value)
}

async function discard (id) {
  try {
    await api.cancelCopilotLogin(id)
  } catch (failure) {
    $q.notify({ type: 'negative', message: `Could not cancel Copilot sign-in: ${failure.message}` })
  }
}

function schedule (interval) {
  clearTimeout(timer)
  timer = setTimeout(poll, Math.min(interval * 1000, Math.max(0, expiresAt - Date.now())))
}

async function start () {
  if (working.value || props.disabled) return
  const current = ++revision
  error.value = ''
  setBusy(true)
  try {
    const result = await api.startCopilotLogin(props.config)
    if (current !== revision) {
      await discard(result.id)
      return
    }
    device.value = result
    expiresAt = Date.now() + result.expires_in * 1000
    schedule(result.interval)
  } catch (failure) {
    if (current === revision) {
      error.value = failure.message
      setBusy(false)
    }
  }
}

async function poll () {
  if (!device.value || polling.value || cancelling.value) return
  clearTimeout(timer)
  const current = revision
  if (Date.now() >= expiresAt) {
    await cancel()
    error.value = 'The GitHub device code expired. Sign in again.'
    return
  }
  polling.value = true
  error.value = ''
  try {
    const result = await api.pollCopilotLogin(device.value.id)
    if (current !== revision) return
    if (result.status === 'complete') {
      const id = device.value.id
      device.value = null
      setBusy(false)
      await discard(id)
      if (current === revision) emit('connected')
    } else if (result.status === 'pending') {
      schedule(result.interval)
    } else {
      throw new Error('Invalid Copilot sign-in response. Try again.')
    }
  } catch (failure) {
    if (current === revision) {
      error.value = failure.message
      if (failure.status && failure.status < 500 && failure.status !== 429) {
        device.value = null
        setBusy(false)
      }
    }
  } finally {
    if (current === revision) polling.value = false
  }
}

async function cancel () {
  ++revision
  clearTimeout(timer)
  const id = device.value?.id
  cancelling.value = true
  if (id) await discard(id)
  device.value = null
  polling.value = false
  cancelling.value = false
  setBusy(false)
}

onBeforeUnmount(() => {
  ++revision
  clearTimeout(timer)
  if (device.value) void discard(device.value.id)
  setBusy(false)
})

defineExpose({ start })
</script>
