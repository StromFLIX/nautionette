<template>
  <div class="shell" :class="{ 'shell--detail': hasSelection }">
    <NavRail class="shell__rail" />

    <aside class="shell__side" :style="{ width: `${sideWidth}px` }">
      <SidePanel />
      <div
        class="shell__grip"
        :class="{ 'shell__grip--active': dragging }"
        @pointerdown="startDrag"
        @dblclick="resetWidth"
      />
    </aside>

    <main class="shell__main">
      <RouterView />
    </main>

    <SettingsDialog />

    <q-dialog v-model="tokenPrompt" persistent>
      <q-card class="token-card">
        <div class="token-card__title">Access token</div>
        <p class="caption muted">
          This instance is protected. Paste the token from your deployment settings.
        </p>
        <input
          v-model="token" class="field" type="password" placeholder="token"
          autofocus @keydown.enter="saveToken"
        />
        <div class="row token-card__actions">
          <button class="btn btn--primary" @click="saveToken">Unlock</button>
        </div>
      </q-card>
    </q-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import NavRail from './components/NavRail.vue'
import SidePanel from './components/SidePanel.vue'
import SettingsDialog from './components/SettingsDialog.vue'
import { actions, store } from './store'
import { auth } from './api'

const WIDTH_KEY = 'nautionette.sideWidth'
const DEFAULT_WIDTH = 336

const route = useRoute()
const sideWidth = ref(Number(localStorage.getItem(WIDTH_KEY)) || DEFAULT_WIDTH)
const dragging = ref(false)
const tokenPrompt = ref(false)
const token = ref(auth.token)

const hasSelection = computed(() => Boolean(route.params.id || route.params.name))

function startDrag (event) {
  dragging.value = true
  const origin = event.clientX
  const start = sideWidth.value
  const move = (moveEvent) => {
    sideWidth.value = Math.min(560, Math.max(260, start + moveEvent.clientX - origin))
  }
  const stop = () => {
    dragging.value = false
    localStorage.setItem(WIDTH_KEY, String(sideWidth.value))
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop)
}

function resetWidth () {
  sideWidth.value = DEFAULT_WIDTH
  localStorage.setItem(WIDTH_KEY, String(DEFAULT_WIDTH))
}

function saveToken () {
  actions.setToken(token.value.trim())
  tokenPrompt.value = false
}

watch(() => store.needsToken, (needed) => { if (needed) tokenPrompt.value = true })

onMounted(() => {
  actions.connect()
  actions.refreshAll()
})
onUnmounted(() => actions.disconnect())
</script>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: var(--rail-width) auto 1fr;
  height: 100%;
  background: var(--surface-app);
}

.shell__side {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--surface-panel);
  border-right: 1px solid var(--border);
}

.shell__grip {
  position: absolute;
  top: 0;
  right: -3px;
  width: 7px;
  height: 100%;
  cursor: col-resize;
  z-index: 5;
}

.shell__grip::after {
  content: '';
  position: absolute;
  inset: 0 3px;
  background: transparent;
  transition: background var(--transition);
}

.shell__grip:hover::after,
.shell__grip--active::after {
  background: var(--accent);
}

.shell__main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  background: var(--surface-app);
}

.token-card {
  width: 380px;
  max-width: 92vw;
  padding: 22px;
}

.token-card__title {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 6px;
}

.token-card p {
  margin: 0 0 14px;
}

.token-card__actions {
  justify-content: flex-end;
  margin-top: 16px;
}

@media (max-width: 900px) {
  .shell {
    grid-template-columns: 1fr;
    grid-template-rows: 1fr auto;
  }

  .shell__side {
    grid-row: 1;
    width: 100% !important;
    border-right: none;
  }

  .shell__grip {
    display: none;
  }

  .shell__main {
    display: none;
  }

  .shell--detail .shell__side {
    display: none;
  }

  .shell--detail .shell__main {
    display: flex;
    grid-row: 1;
  }

  .shell__rail {
    grid-row: 2;
  }
}
</style>
