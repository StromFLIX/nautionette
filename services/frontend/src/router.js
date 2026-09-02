import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/chats' },
  { path: '/chats/:id?', name: 'chats', component: () => import('./views/ChatPane.vue') },
  { path: '/workflows/:name?', name: 'workflows', component: () => import('./views/WorkflowPane.vue') },
  { path: '/runs/:id?', name: 'runs', component: () => import('./views/RunPane.vue') },
  { path: '/settings/:tab?', name: 'settings', component: () => import('./views/SettingsPane.vue') },
  { path: '/:catchAll(.*)', redirect: '/chats' }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// The list a view sits on top of, and so the place its back button belongs.
const UNDER = { chats: '/chats', workflows: '/workflows', runs: '/runs', settings: '/chats' }

/**
 * Opening straight onto a detail view (deep link, reload, Android cold start) leaves
 * nothing underneath it, so a back gesture would leave the app. Put its list under it
 * before the first render.
 */
export async function seedHistory () {
  const entry = router.resolve(window.location.pathname + window.location.search)
  const list = UNDER[entry.name]
  if (!list || entry.path === list) return
  await router.replace(list)
  await router.push(entry.fullPath)
}

/** Back out of a detail view without stacking history when that is where we came from. */
export function backTo (path) {
  if (window.history.state?.back === path) router.back()
  else router.push(path)
}

export default router
