<template>
  <q-dialog v-model="open" @hide="actions.closeSettings()">
    <q-card class="settings">
      <aside class="settings__nav">
        <div class="settings__brand">
          <img src="/favicon.svg" width="20" height="20" alt="" />
          <span>Settings</span>
        </div>
        <button
          v-for="item in TABS" :key="item.key" class="settings__tab"
          :class="{ 'settings__tab--active': tab === item.key }" @click="tab = item.key"
        >
          <span class="material-icons">{{ item.icon }}</span>{{ item.label }}
        </button>
        <div class="settings__version caption dim">v{{ store.system.version || 'dev' }}</div>
      </aside>

      <div class="settings__body scroll-y">
        <!-- Each pane loads what it needs when it is shown, and is torn down with the tab. -->
        <component :is="pane" :key="tab" />
      </div>

      <button class="settings__close btn btn--icon" @click="actions.closeSettings()">
        <span class="material-icons">close</span>
      </button>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import ActivityPane from './settings/ActivityPane.vue'
import AgentsPane from './settings/AgentsPane.vue'
import GeneralPane from './settings/GeneralPane.vue'
import McpPane from './settings/McpPane.vue'
import SystemPane from './settings/SystemPane.vue'
import { actions, store } from '../store'

const TABS = [
  { key: 'general', label: 'General', icon: 'tune', pane: GeneralPane },
  { key: 'agents', label: 'Agents', icon: 'smart_toy', pane: AgentsPane },
  { key: 'mcp', label: 'MCP servers', icon: 'handyman', pane: McpPane },
  { key: 'system', label: 'System', icon: 'monitor_heart', pane: SystemPane },
  { key: 'activity', label: 'Activity', icon: 'bolt', pane: ActivityPane }
]

const tab = ref(store.settingsTab)

const pane = computed(() => TABS.find((item) => item.key === tab.value).pane)

const open = computed({
  get: () => store.settingsOpen,
  set: (value) => { store.settingsOpen = value }
})

watch(() => store.settingsTab, (value) => { tab.value = value })
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

.settings__close {
  position: absolute;
  top: 10px;
  right: 10px;
}

@media (max-width: 760px) {
  .settings {
    grid-template-columns: 1fr;
    grid-template-rows: auto 1fr;
    width: 100%;
    max-width: 100%;
    height: calc(
      var(--app-height) - max(24px, env(safe-area-inset-top)) -
        max(24px, env(safe-area-inset-bottom))
    );
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
