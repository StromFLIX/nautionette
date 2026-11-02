<template>
  <nav v-if="apps.length" class="app-shortcuts" aria-label="Pinned apps">
    <span class="app-shortcuts__label">Apps</span>
    <RouterLink v-for="app in apps" :key="app.id" :to="appLocation(app, route.query.agent)" class="app-shortcuts__link" :aria-label="`Open ${app.title}, pinned release ${app.pinned_revision || app.active_revision}`">
      <AppAvatar :app="app" />
      <span class="app-shortcuts__name truncate">{{ app.title }}</span>
      <q-tooltip>{{ app.title }} · v{{ app.pinned_revision || app.active_revision }}</q-tooltip>
    </RouterLink>
    <RouterLink class="app-shortcuts__all" :to="{ path: '/apps', query: route.query.agent ? { agent: route.query.agent } : {} }" aria-label="Browse apps" title="Browse apps"><span class="material-icons" aria-hidden="true">arrow_forward</span></RouterLink>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { store } from '../store'
import { appLocation, pinnedApps } from '../miniapps'
import AppAvatar from './AppAvatar.vue'
const route = useRoute()
const apps = computed(() => pinnedApps(store.apps, route.query.agent))
</script>

<style scoped>
.app-shortcuts { display: flex; align-items: center; flex: none; gap: 8px; padding: 8px 18px; border-bottom: 1px solid var(--border); background: var(--surface-panel); min-width: 0; }
.app-shortcuts__label { color: var(--text-dim); font-size: .6875rem; margin-right: 4px; }
.app-shortcuts__link { display: flex; align-items: center; gap: 7px; padding: 4px 8px 4px 4px; min-width: 0; max-width: 160px; border: 1px solid transparent; border-radius: var(--radius-sm); text-decoration: none; color: var(--text-muted); --app-avatar-size: 30px; }
.app-shortcuts__name { font-size: .75rem; }
.app-shortcuts__link:hover, .app-shortcuts__all:hover { background: var(--accent-soft); color: var(--accent); border-color: var(--border-strong); }
.app-shortcuts__link:focus-visible, .app-shortcuts__all:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.app-shortcuts__all { margin-left: auto; display: grid; place-items: center; width: 36px; height: 36px; flex: none; border: 1px solid transparent; border-radius: var(--radius-sm); color: var(--text-dim); text-decoration: none; }
.app-shortcuts__all .material-icons { font-size: 1.125rem; }
@media (max-width: 1100px) { .app-shortcuts__name { display: none; } .app-shortcuts__link { padding: 4px; } }
@media (max-width: 900px) { .app-shortcuts { gap: 3px; padding: calc(4px + env(safe-area-inset-top)) 8px 4px; } .app-shortcuts__label { display: none; } .app-shortcuts__link { --app-avatar-size: 30px; min-width: 38px; min-height: 40px; justify-content: center; } }
</style>
