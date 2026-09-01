import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/chats' },
  { path: '/chats/:id?', name: 'chats', component: () => import('./pages/ChatsPage.vue') },
  { path: '/workflows/:name?', name: 'workflows', component: () => import('./pages/WorkflowsPage.vue') },
  { path: '/runs', name: 'runs', component: () => import('./pages/RunsPage.vue') },
  { path: '/system', name: 'system', component: () => import('./pages/SystemPage.vue') },
  { path: '/:catchAll(.*)', redirect: '/chats' }
]

export default createRouter({
  history: createWebHistory(),
  routes
})
