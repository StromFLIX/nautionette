<template>
  <nav class="rail">
    <RouterLink to="/chats" class="rail__brand" aria-label="Nautionette">
      <img src="/favicon.svg" width="26" height="26" alt="" />
    </RouterLink>

    <div class="rail__nav">
      <RouterLink
        v-for="item in items" :key="item.name" :to="item.to" class="rail__item"
        :class="{ 'rail__item--active': active === item.name }"
      >
        <span class="material-icons">{{ item.icon }}</span>
        <span class="rail__label">{{ item.label }}</span>
        <span v-if="item.badge" class="rail__badge">{{ item.badge }}</span>
      </RouterLink>
    </div>

    <div class="rail__foot">
      <button class="rail__item rail__item--plain" @click="actions.openSettings('system')">
        <span class="dot" :class="healthClass" />
        <span class="rail__label">{{ healthLabel }}</span>
        <q-tooltip anchor="center right" self="center left">System status</q-tooltip>
      </button>
      <button class="rail__item" @click="actions.openSettings('general')">
        <span class="material-icons">settings</span>
        <span class="rail__label">Settings</span>
      </button>
    </div>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { actions, draftCount, health } from '../store'

const route = useRoute()
const active = computed(() => route.name)

const items = computed(() => [
  { name: 'chats', label: 'Chats', icon: 'chat_bubble', to: '/chats' },
  { name: 'workflows', label: 'Flows', icon: 'account_tree', to: '/workflows', badge: draftCount.value || 0 },
  { name: 'runs', label: 'Runs', icon: 'history', to: '/runs' }
])

const healthClass = computed(() => ({
  ok: 'dot--ok',
  degraded: 'dot--bad',
  unknown: ''
}[health.value]))

const healthLabel = computed(() => (health.value === 'ok' ? 'Healthy' : health.value === 'degraded' ? 'Issues' : 'Status'))
</script>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 10px 0;
  background: var(--surface-rail);
  border-right: 1px solid var(--border);
}

.rail__brand {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  margin-bottom: 10px;
  border-radius: var(--radius-md);
  opacity: 0.9;
}

.rail__nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  align-items: center;
}

.rail__foot {
  margin-top: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  align-items: center;
}

.rail__item {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  width: 54px;
  height: 50px;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--text-dim);
  font: inherit;
  cursor: pointer;
  text-decoration: none;
  transition: background var(--transition), color var(--transition);
}

.rail__item:hover {
  background: var(--surface-hover);
  color: var(--text);
}

.rail__item--active {
  background: var(--accent-soft);
  color: var(--accent-hover);
}

.rail__item--plain {
  height: 40px;
}

.rail__label {
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.01em;
}

.rail__badge {
  position: absolute;
  top: 4px;
  right: 8px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: var(--radius-pill);
  background: var(--warning);
  color: #16181d;
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  text-align: center;
}

@media (max-width: 900px) {
  .rail {
    flex-direction: row;
    justify-content: space-around;
    padding: 4px 8px;
    border-right: none;
    border-top: 1px solid var(--border);
    padding-bottom: max(4px, env(safe-area-inset-bottom));
  }

  .rail__brand {
    display: none;
  }

  .rail__nav,
  .rail__foot {
    flex-direction: row;
    width: auto;
    margin: 0;
  }
}
</style>
