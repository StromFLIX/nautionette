<template>
  <nav class="rail" aria-label="Main navigation">
    <RouterLink to="/chats" class="rail__brand" aria-label="Nautionette">
      <BrandMark />
    </RouterLink>

    <div class="rail__nav">
      <RouterLink
        v-for="item in items" :key="item.name" :to="item.to" class="rail__item"
        :class="{ 'rail__item--active': active === item.name }" :aria-current="active === item.name ? 'page' : undefined"
      >
        <span class="material-icons" aria-hidden="true">{{ item.icon }}</span>
        <span class="rail__label">{{ item.label }}</span>
        <span v-if="item.badge" class="rail__badge">{{ item.badge }}</span>
      </RouterLink>
    </div>

    <RouterLink
      class="rail__item rail__settings" :class="{ 'rail__item--active': active === 'settings' }"
      :aria-current="active === 'settings' ? 'page' : undefined" :to="`/settings/${health === 'degraded' ? 'system' : 'general'}`"
      :title="health === 'degraded' ? 'Settings — something needs attention' : 'Settings'"
      aria-label="Settings"
    >
      <span class="material-icons" aria-hidden="true">settings</span>
      <span class="rail__label">Settings</span>
      <span v-if="health !== 'ok'" class="dot rail__settings-dot" :class="{ 'dot--bad': health === 'degraded' }" />
    </RouterLink>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { draftCount, health } from '../store'
import BrandMark from './BrandMark.vue'

const route = useRoute()
const active = computed(() => route.name)

const items = computed(() => [
  { name: 'chats', label: 'Chats', icon: 'chat_bubble_outline', to: '/chats' },
  { name: 'workflows', label: 'Workflows', icon: 'account_tree', to: '/workflows', badge: draftCount.value || 0 },
  { name: 'runs', label: 'Runs', icon: 'history', to: '/runs' }
])
</script>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 16px 0 10px;
  background: var(--surface-rail);
  border-right: 1px solid var(--border);
}

.rail__brand {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  margin-bottom: 18px;
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

.rail__item {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  width: calc(100% - 12px);
  height: 56px;
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

.rail__item--active::before {
  content: '';
  position: absolute;
  left: -6px;
  width: 2px;
  height: 18px;
  border-radius: var(--radius-pill);
  background: var(--accent);
}

.rail__settings {
  margin-top: auto;
  flex-shrink: 0;
}

.rail__settings-dot {
  position: absolute;
  top: 6px;
  right: 14px;
  border: 2px solid var(--surface-rail);
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
  color: var(--warning-text);
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  text-align: center;
}

@media (max-width: 900px) {
  .rail {
    flex-direction: row;
    align-items: stretch;
    gap: 0;
    min-width: 0;
    height: calc(var(--mobile-nav-height) + env(safe-area-inset-bottom));
    padding: 4px max(4px, env(safe-area-inset-right)) max(4px, env(safe-area-inset-bottom)) max(4px, env(safe-area-inset-left));
    border-right: none;
    border-top: 1px solid var(--border);
  }

  .rail__brand,
  .rail__settings {
    display: none;
  }

  .rail__nav {
    display: flex;
    flex: 1 1 auto;
    flex-direction: row;
    align-items: stretch;
    gap: 0;
    min-width: 0;
    width: auto;
    margin: 0;
  }

  .rail__item {
    flex: 1 1 0;
    width: auto;
    min-width: 0;
    height: 50px;
    border-radius: var(--radius-sm);
  }

  .rail__badge {
    right: max(8px, calc(50% - 24px));
  }

  .rail__item--active::before { display: none; }
}
</style>
