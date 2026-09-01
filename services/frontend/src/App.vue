<template>
  <q-layout view="hHh LpR lFf">
    <q-header class="q-py-xs">
      <q-toolbar>
        <q-btn dense flat round icon="menu" class="lt-md" @click="drawer = !drawer" />
        <q-toolbar-title class="row items-center no-wrap">
          <img src="/favicon.svg" width="26" height="26" class="q-mr-sm" alt="" />
          <span class="text-weight-bold">Nautionette</span>
          <span class="text-caption text-grey-6 q-ml-md gt-sm">chats and workflows are the same thing</span>
        </q-toolbar-title>

        <q-chip
          v-for="component in status.components || []"
          :key="component.name"
          dense square size="sm" class="gt-sm"
          :color="component.status === 'ok' ? 'green-9' : 'red-9'"
          text-color="white"
        >
          {{ component.name }}
        </q-chip>
        <q-btn dense flat round icon="refresh" :loading="loading" @click="loadStatus">
          <q-tooltip>Refresh status</q-tooltip>
        </q-btn>
        <q-btn dense flat round icon="key" @click="askToken">
          <q-tooltip>Access token</q-tooltip>
        </q-btn>
      </q-toolbar>
    </q-header>

    <q-drawer v-model="drawer" show-if-above :width="240" bordered>
      <q-list padding>
        <q-item-label header class="text-grey-6">Workspace</q-item-label>
        <q-item clickable v-ripple :to="'/chats'" active-class="text-primary">
          <q-item-section avatar><q-icon name="forum" /></q-item-section>
          <q-item-section>Chats</q-item-section>
        </q-item>
        <q-item clickable v-ripple :to="'/workflows'" active-class="text-primary">
          <q-item-section avatar><q-icon name="account_tree" /></q-item-section>
          <q-item-section>Workflows</q-item-section>
          <q-item-section side v-if="draftCount">
            <q-badge color="orange">{{ draftCount }}</q-badge>
          </q-item-section>
        </q-item>
        <q-item clickable v-ripple :to="'/runs'" active-class="text-primary">
          <q-item-section avatar><q-icon name="history" /></q-item-section>
          <q-item-section>Runs</q-item-section>
        </q-item>
        <q-item clickable v-ripple :to="'/system'" active-class="text-primary">
          <q-item-section avatar><q-icon name="monitor_heart" /></q-item-section>
          <q-item-section>System</q-item-section>
        </q-item>
      </q-list>

      <template v-if="!status.model_key_present">
        <q-separator class="q-my-sm" />
        <div class="q-pa-md text-caption text-orange-4">
          No model key is configured, so agent calls will fail. Set
          <code>OPENROUTER_API_KEY</code> and restart the gateway.
        </div>
      </template>
    </q-drawer>

    <q-page-container>
      <router-view :status="status" @changed="loadStatus" />
    </q-page-container>
  </q-layout>
</template>

<script setup>
import { onMounted, onUnmounted, provide, ref } from 'vue'
import { useQuasar } from 'quasar'
import { api, auth, liveEvents } from './api'

const $q = useQuasar()
const drawer = ref(false)
const status = ref({ components: [] })
const draftCount = ref(0)
const loading = ref(false)
const listeners = new Set()
let source = null

provide('onLiveEvent', (fn) => {
  listeners.add(fn)
  return () => listeners.delete(fn)
})

async function loadStatus () {
  loading.value = true
  try {
    status.value = await api.system()
    const { drafts } = await api.drafts()
    draftCount.value = drafts.length
  } catch (error) {
    if (error.status === 401) askToken(true)
  } finally {
    loading.value = false
  }
}

function askToken (forced = false) {
  $q.dialog({
    title: forced ? 'Access token required' : 'Access token',
    message: 'This instance is protected. Paste the token from your deployment settings.',
    prompt: { model: auth.token, type: 'password', outlined: true },
    cancel: true,
    persistent: forced
  }).onOk((value) => {
    auth.token = value.trim()
    connect()
    loadStatus()
  })
}

function connect () {
  source?.close()
  source = liveEvents((event) => {
    if (event.kind === 'promote.draft' || event.kind === 'workflow.published') loadStatus()
    listeners.forEach((fn) => fn(event))
  })
}

onMounted(() => {
  connect()
  loadStatus()
})
onUnmounted(() => source?.close())
</script>
