<template>
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
const historyMode = ref('auto')
const form = reactive({ default_model: '', default_agent_set: '', history_chars: 0 })
const defaults = reactive({ default_model: '', default_agent_set: '', history_chars: 0 })

const contextHint = computed(() => {
  if (historyMode.value === 'fixed') {
    return `${Math.round((form.history_chars || 0) / 4000)}k tokens`
  }
  const model = (store.catalog.models || []).find((item) => item.id === form.default_model)
  if (!model?.context_length) return 'this model publishes no window; the fallback is used'
  const window = compactChars(model.context_length)
  return `${window} token window \u2192 ${compactChars(historyBudget(form.default_model))} chars`
})

function apply (data) {
  Object.assign(form, data.settings)
  historyMode.value = data.settings.history_chars > 0 ? 'fixed' : 'auto'
}

function saveToken () {
  actions.setToken(token.value.trim())
  $q.notify({ type: 'positive', message: 'Token saved' })
}

function saveServer () {
  actions.setServer(serverUrl.value)
  const message = serverUrl.value.trim() ? 'Server saved' : 'Using this origin'
  $q.notify({ type: 'positive', message })
}

async function saveSettings () {
  saving.value = true
  try {
    apply(await api.saveSettings({
      ...form,
      history_chars: historyMode.value === 'auto' ? 0 : form.history_chars
    }))
    await actions.loadCatalog(true)
    $q.notify({ type: 'positive', message: 'Saved' })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    saving.value = false
  }
}

async function resetSettings () {
  const cleared = { default_model: null, default_agent_set: null, history_chars: null }
  apply(await api.saveSettings(cleared))
  await actions.loadCatalog(true)
}

onMounted(async () => {
  const data = await api.settings()
  Object.assign(defaults, data.defaults)
  apply(data)
})
</script>

<style scoped>
.settings__save {
  position: sticky;
  bottom: -32px;
  margin-top: 24px;
  padding: 12px 0;
  border-top: 1px solid var(--border);
  background: var(--surface-overlay);
}

@media (max-width: 760px) {
  .settings__save {
    flex-wrap: wrap;
  }

  .settings__save .grow {
    flex-basis: 100%;
  }
}
</style>
