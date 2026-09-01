import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/chats' },
  { path: '/chats/:id?', name: 'chats', component: () => import('./views/ChatPane.vue') },
  { path: '/workflows/:name?', name: 'workflows', component: () => import('./views/WorkflowPane.vue') },
  { path: '/runs/:id?', name: 'runs', component: () => import('./views/RunPane.vue') },
  { path: '/:catchAll(.*)', redirect: '/chats' }
]

export default createRouter({
  history: createWebHistory(),
  routes
})
