import { computed, ref } from 'vue'
import { chatCacheScope } from './chat-cache'
import { dashboardOrder } from './dashboard'

const version = ref(0)
const key = () => `nautionette.dashboard-order:${chatCacheScope()}`
function saved () {
  try { const data = JSON.parse(localStorage.getItem(key()) || '[]'); return Array.isArray(data) ? data.filter(id => typeof id === 'string').slice(0, 1000) : [] } catch { return [] }
}
export function orderedDashboards (getItems) {
  return computed(() => { void version.value; return dashboardOrder(getItems(), saved()) })
}
export function moveDashboard (items, id, direction) {
  const ids = dashboardOrder(items, saved()).map(item => item.id)
  const index = ids.indexOf(id), next = index + direction
  if (index < 0 || next < 0 || next >= ids.length) return
  ;[ids[index], ids[next]] = [ids[next], ids[index]]
  localStorage.setItem(key(), JSON.stringify(ids))
  version.value++
}
window.addEventListener('storage', event => { if (event.key === key()) version.value++ })
