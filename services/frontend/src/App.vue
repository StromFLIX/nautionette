<template>
  <div class="app-frame">
    <EnvironmentBanner />
  <div class="shell" :class="{ 'shell--detail': hasSelection, 'shell--full': fullPage }">
    <NavRail class="shell__rail" />
    <template v-if="!fullPage">
      <aside class="shell__side" :style="{ width: `${sideWidth}px` }">
        <SidePanel />
        <div
          class="shell__grip"
          :class="{ 'shell__grip--active': dragging }"
          role="separator" aria-label="Resize sidebar" aria-orientation="vertical"
          :aria-valuenow="sideWidth" :aria-valuemin="260" :aria-valuemax="560" tabindex="0"
          @pointerdown="startDrag" @keydown="resizeKeys"
          @dblclick="resetWidth"
        />
      </aside>
    </template>

    <main class="shell__main">
      <RouterView />
    </main>

    <q-dialog v-model="gate" persistent>
      <q-card class="token-card">
        <div class="token-card__title">Connect</div>
        <p class="caption muted">
          {{ isNative
            ? 'Point the app at your instance, then paste its access token.'
            : 'This instance is protected. Paste the token from your deployment settings.' }}
        </p>
        <input
          v-if="isNative" v-model="serverUrl" class="field" style="margin-bottom: 8px"
          type="url" inputmode="url" placeholder="https://nautionette.example.com"
        />
        <input
          v-model="token" class="field" type="password" placeholder="access token"
          autofocus @keydown.enter="connect"
        />
        <div class="row token-card__actions">
          <button class="btn btn--primary" :disabled="isNative && !serverUrl.trim()" @click="connect">
            Connect
          </button>
        </div>
      </q-card>
    </q-dialog>
  </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import EnvironmentBanner from './components/EnvironmentBanner.vue'
import NavRail from './components/NavRail.vue'
import SidePanel from './components/SidePanel.vue'
import { actions, store } from './store'
import { auth, isNative, server } from './api'
import { currentTokens, preferences } from './preferences'
import { syncSystemBars } from './system-bars'

const DEFAULT_WIDTH = 320
const route = useRoute()
const router = useRouter()
const sideWidth = computed({ get: () => preferences.sideWidth, set: value => { preferences.sideWidth = value } })
const dragging = ref(false)
const gate = ref(store.needsServer)
const token = ref(auth.token)
const serverUrl = ref(server.url)

const hasSelection = computed(() => Boolean(route.params.id || route.params.name))
// Settings replaces the list, but keeps the desktop navigation in reach.
const fullPage = computed(() => route.name === 'settings')
const mobileMedia = window.matchMedia('(max-width: 900px)')
const mobile = ref(mobileMedia.matches)
const updateMobile = event => { mobile.value = event.matches }

watch([() => preferences.theme, currentTokens, hasSelection, fullPage, mobile],
  ([theme, tokens, detail, full, narrow]) => syncSystemBars(theme, tokens, narrow && !detail && !full),
  { immediate: true, flush: 'post' })

let stopDrag = () => {}
function startDrag (event) {
  if (event.button !== 0) return
  event.preventDefault()
  stopDrag()
  dragging.value = true
  const origin = event.clientX
  const start = sideWidth.value
  const move = (moveEvent) => {
    sideWidth.value = Math.round(Math.min(560, Math.max(260, start + moveEvent.clientX - origin)))
  }
  stopDrag = () => {
    dragging.value = false
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stopDrag)
    window.removeEventListener('pointercancel', stopDrag)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stopDrag)
  window.addEventListener('pointercancel', stopDrag)
}

function resetWidth () {
  sideWidth.value = DEFAULT_WIDTH
}

function resizeKeys (event) {
  if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return
  event.preventDefault()
  if (event.key === 'Home') resetWidth()
  else sideWidth.value = Math.min(560, Math.max(260, sideWidth.value + (event.key === 'ArrowRight' ? 10 : -10)))
}

function shortcuts (event) {
  if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key === ',') {
    event.preventDefault()
    router.push('/settings/general')
  }
}

function connect () {
  if (isNative) actions.setServer(serverUrl.value)
  actions.setToken(token.value.trim())
  gate.value = false
}

watch(() => store.needsToken || store.needsServer, (needed) => { if (needed) gate.value = true })

onMounted(() => {
  mobileMedia.addEventListener('change', updateMobile)
  actions.connect()
  actions.refreshAll()
  window.addEventListener('keydown', shortcuts)
})
onUnmounted(() => {
  mobileMedia.removeEventListener('change', updateMobile)
  actions.disconnect()
  stopDrag()
  window.removeEventListener('keydown', shortcuts)
})
</script>

<style scoped>
.app-frame {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.shell {
  flex: 1;
  display: grid;
  grid-template-columns: var(--rail-width) auto minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
  height: 100%;
  min-width: 0;
  min-height: 0;
  background: var(--surface-app);
}

.shell--full {
  grid-template-columns: var(--rail-width) minmax(0, 1fr);
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
  font-size: 1rem;
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
    grid-template-rows: minmax(0, 1fr) auto;
  }

  .shell--full {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr);
  }

  .shell--full .shell__rail { display: none; }

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

  .shell--detail .shell__main,
  .shell--full .shell__main {
    display: flex;
    grid-row: 1;
  }

  /* A detail view is the whole screen: its own back button is the way out. */
  .shell--detail .shell__rail {
    display: none;
  }

  .shell__rail {
    grid-row: 2;
  }

  .token-card {
    width: min(380px, 100%);
    max-width: 100%;
    padding: 18px;
  }
}
</style>
