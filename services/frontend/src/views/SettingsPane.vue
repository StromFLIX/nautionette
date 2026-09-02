<template>
  <div class="settings stack grow">
    <header class="pane-head">
      <button class="btn btn--icon" title="Back" @click="backTo('/chats')">
        <span class="material-icons">arrow_back</span>
      </button>
      <div class="pane-head__title grow truncate">Settings</div>
      <span class="caption dim">v{{ store.system.version || 'dev' }}</span>
    </header>

    <nav class="settings__tabs">
      <RouterLink
        v-for="item in TABS" :key="item.key" :to="`/settings/${item.key}`" replace
        class="settings__tab" :class="{ 'settings__tab--active': tab === item.key }"
      >
        <span class="material-icons">{{ item.icon }}</span>{{ item.label }}
        <span v-if="item.key === 'system' && health === 'degraded'" class="dot dot--bad" />
      </RouterLink>
    </nav>

    <div class="settings__body scroll-y grow">
      <div class="settings__inner">
        <!-- Each pane loads what it needs when it is shown, and is torn down with the tab. -->
        <component :is="pane" :key="tab" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import ActivityPane from '../components/settings/ActivityPane.vue'
import AgentsPane from '../components/settings/AgentsPane.vue'
import GeneralPane from '../components/settings/GeneralPane.vue'
import McpPane from '../components/settings/McpPane.vue'
import SystemPane from '../components/settings/SystemPane.vue'
import { backTo } from '../router'
import { health, store } from '../store'

const TABS = [
  { key: 'general', label: 'General', icon: 'tune', pane: GeneralPane },
  { key: 'agents', label: 'Agents', icon: 'smart_toy', pane: AgentsPane },
  { key: 'mcp', label: 'MCP servers', icon: 'handyman', pane: McpPane },
  { key: 'system', label: 'System', icon: 'monitor_heart', pane: SystemPane },
  { key: 'activity', label: 'Activity', icon: 'bolt', pane: ActivityPane }
]

const route = useRoute()
const tab = computed(() => (TABS.some((item) => item.key === route.params.tab) ? route.params.tab : 'general'))
const pane = computed(() => TABS.find((item) => item.key === tab.value).pane)
</script>

<style scoped>
.settings {
  min-width: 0;
  overflow: hidden;
}

.settings__tabs {
  display: flex;
  flex: none;
  gap: 2px;
  padding: 6px 10px;
  overflow-x: auto;
  scrollbar-width: none;
  border-bottom: 1px solid var(--border);
}

.settings__tab {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: none;
  padding: 7px 12px;
  border-radius: var(--radius-pill);
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
  white-space: nowrap;
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

.settings__body {
  padding: 22px 26px 32px;
}

.settings__inner {
  max-width: 720px;
  margin: 0 auto;
}

@media (max-width: 900px) {
  .settings__tabs {
    padding-right: max(10px, env(safe-area-inset-right));
    padding-left: max(10px, env(safe-area-inset-left));
  }

  .settings__body {
    padding: 18px max(16px, env(safe-area-inset-right))
      calc(24px + env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left));
  }
}

@media (max-width: 420px) {
  .settings__tab {
    padding: 7px 10px;
  }

  .settings__tab .material-icons {
    display: none;
  }
}
</style>
