<template>
  <nav v-if="apps.length" class="app-shortcuts" :class="{ 'app-shortcuts--compact': compact }" aria-label="Pinned apps">
    <span v-if="!compact" class="app-shortcuts__label">Apps</span>
    <RouterLink v-for="app in apps" :key="app.id" :to="appLocation(app, agentId)" class="app-shortcuts__link" :class="{ 'app-shortcuts__link--active': route.name === 'apps' && route.params.id === app.id }" :aria-label="`Open ${app.title}, pinned release ${app.pinned_revision || app.active_revision}`" :aria-current="route.name === 'apps' && route.params.id === app.id ? 'page' : undefined">
      <AppAvatar :app="app" />
      <span v-if="!compact" class="app-shortcuts__name truncate">{{ app.title }}</span>
      <q-tooltip>{{ app.title }} · {{ appOwner(app, store.agentNavigation.agents) }} · v{{ app.pinned_revision || app.active_revision }}</q-tooltip>
    </RouterLink>
    <RouterLink class="app-shortcuts__all" :to="{ path: '/apps', query: agentId ? { agent: agentId } : {} }" aria-label="Browse apps" title="Browse apps"><span class="material-icons" aria-hidden="true">arrow_forward</span></RouterLink>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { store } from '../store'
import { appLocation, appOwner, pinnedApps } from '../miniapps'
import AppAvatar from './AppAvatar.vue'
defineProps({ compact: { type: Boolean, default: false } })
const route = useRoute()
const agentId = computed(() => typeof route.query.agent === 'string' ? route.query.agent : null)
const apps = computed(() => pinnedApps(store.apps, agentId.value))
</script>

<style scoped>
.app-shortcuts { display: flex; align-items: center; flex: none; gap: 8px; padding: 8px 18px; border-bottom: 1px solid var(--border); background: var(--surface-panel); min-width: 0; }
.app-shortcuts__label { color: var(--text-dim); font-size: .6875rem; margin-right: 4px; }
.app-shortcuts__link { display: flex; align-items: center; gap: 7px; padding: 4px 8px 4px 4px; min-width: 0; max-width: 160px; border: 1px solid transparent; border-radius: var(--radius-sm); text-decoration: none; color: var(--text-muted); --app-avatar-size: 30px; }
.app-shortcuts__name { font-size: .75rem; }
.app-shortcuts__link--active, .app-shortcuts__link:hover, .app-shortcuts__all:hover { background: var(--accent-soft); color: var(--accent); border-color: var(--border-strong); }
.app-shortcuts__link:focus-visible, .app-shortcuts__all:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.app-shortcuts__all { margin-left: auto; display: grid; place-items: center; width: 36px; height: 36px; flex: none; border: 1px solid transparent; border-radius: var(--radius-sm); color: var(--text-dim); text-decoration: none; }
.app-shortcuts__all .material-icons { font-size: 1.125rem; }
@media (max-width: 1100px) { .app-shortcuts__name { display: none; } .app-shortcuts__link { padding: 4px; } }
@media (max-width: 900px) { .app-shortcuts { gap: 3px; padding: calc(4px + env(safe-area-inset-top)) 8px 4px; } .app-shortcuts__label { display: none; } .app-shortcuts__link { --app-avatar-size: 30px; min-width: 44px; min-height: 44px; justify-content: center; } .app-shortcuts__all { width: 44px; height: 44px; } }
/* Search-adjacent avatars stay usable even at the minimum sidebar width. */
.app-shortcuts--compact { gap: 4px; padding: 10px 2px 3px; border-bottom: 0; background: transparent; overflow-x: auto; scrollbar-width: thin; }
.app-shortcuts--compact .app-shortcuts__link { display: grid; place-items: center; flex: none; width: 44px; min-width: 44px; height: 44px; padding: 3px; --app-avatar-size: 36px; }
.app-shortcuts--compact .app-shortcuts__all { width: 44px; height: 44px; }
</style>
