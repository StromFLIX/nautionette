<template>
  <div class="settings stack grow">
    <header class="pane-head settings__head">
      <button class="btn btn--icon" title="Back to workspace" aria-label="Back to workspace" @click="back">
        <span class="material-icons" aria-hidden="true">arrow_back</span>
      </button>
      <div class="settings__breadcrumb grow"><span>Workspace</span><span aria-hidden="true">/</span><h1>Settings</h1></div>
      <kbd class="settings__shortcut">⌘ / Ctrl ,</kbd>
      <span class="caption dim">v{{ store.system.version || 'dev' }}</span>
    </header>

    <div class="settings__searchbar">
      <div class="settings__search">
        <span class="material-icons" aria-hidden="true">search</span>
        <input ref="searchInput" v-model="query" type="search" placeholder="Search settings and Pi packages…" aria-label="Search settings"
          autocomplete="off" spellcheck="false" @keydown.esc.prevent="clearSearch" />
        <span v-if="searching" class="settings__result-count" role="status">{{ results.length }} found</span>
        <button v-if="query" class="btn btn--icon" aria-label="Clear settings search" @click="clearSearch">
          <span class="material-icons" aria-hidden="true">close</span>
        </button>
        <kbd v-else class="settings__search-key">/</kbd>
      </div>
      <label class="settings__mobile-nav">
        <span class="sr-only">Settings category</span>
        <select class="field" :value="tab" @change="openSection($event.target.value)">
          <optgroup v-for="group in SETTINGS_GROUPS" :key="group" :label="group">
            <option v-for="item in sectionsIn(group)" :key="item.key" :value="item.key">{{ item.label }}</option>
          </optgroup>
        </select>
      </label>
    </div>

    <div class="settings__workspace grow">
      <nav class="settings__tree scroll-y" aria-label="Settings categories">
        <section v-for="group in SETTINGS_GROUPS" :key="group" class="settings__group">
          <h2 class="section-label">{{ group }}</h2>
          <RouterLink v-for="item in sectionsIn(group)" :key="item.key" :to="`/settings/${item.key}`" replace
            class="settings__category" :class="{ 'settings__category--active': !searching && tab === item.key }"
            :aria-current="!searching && tab === item.key ? 'page' : undefined" @click="query = ''">
            <span class="material-icons" aria-hidden="true">{{ item.icon }}</span>
            <span class="grow">{{ item.label }}</span>
            <span v-if="searching && resultCount(item.key)" class="settings__count">{{ resultCount(item.key) }}</span>
            <span v-else-if="item.key === 'system' && health === 'degraded'" class="dot dot--bad" aria-label="Needs attention" />
          </RouterLink>
        </section>
        <div class="settings__tree-foot"><span class="material-icons" aria-hidden="true">tune</span>Your workspace. Your way.</div>
      </nav>

      <div ref="body" class="settings__body scroll-y">
        <div class="settings__inner">
          <section v-if="searching" aria-label="Settings search results">
            <div class="settings__section-head"><h2 class="settings__title">Search results</h2><span class="caption dim">All categories</span></div>
            <p v-if="!results.length" class="settings__no-results">No settings match “{{ query }}”. <button class="btn btn--sm" @click="clearSearch">Clear search</button></p>
            <RouterLink v-for="item in results" :key="`${item.section.key}-${item.id}-${item.label}`"
              :to="{ path: `/settings/${item.section.key}`, hash: `#${item.id}` }" replace class="settings__result" @click="query = ''">
              <span class="material-icons settings__result-icon" aria-hidden="true">{{ item.section.icon }}</span>
              <div class="grow">
                <span class="settings__result-path">{{ item.section.group }} / {{ item.section.label }}</span>
                <h3>{{ item.label }}</h3><p>{{ item.description }}</p>
              </div>
              <span class="settings__scope">{{ item.scope }}</span>
              <span class="material-icons settings__result-arrow" aria-hidden="true">arrow_forward</span>
            </RouterLink>
            <PackageSearch v-if="query.trim().length >= 2" :query="query" @select="choosePackage" />
          </section>
          <!-- Search never discards the current pane's in-progress form. -->
          <div v-show="!searching">
            <div class="settings__scope-line"><span>{{ section.group }}</span><span class="settings__scope">{{ section.scope }}</span></div>
            <component :is="panes[tab]" ref="pane" :key="tab" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { health, store } from '../store'
import { SETTINGS_GROUPS, SETTINGS_SECTIONS, searchSettings } from '../settings-registry'
import PackageSearch from '../components/settings/PackageSearch.vue'

const route = useRoute()
const router = useRouter()
const query = ref(typeof route.query.q === 'string' ? route.query.q : '')
const searchInput = ref(null)
const body = ref(null)
const pane = ref(null)
const panes = Object.fromEntries(SETTINGS_SECTIONS.map(item => [item.key, defineAsyncComponent(item.load)]))
const section = computed(() => SETTINGS_SECTIONS.find(item => item.key === route.params.tab) || SETTINGS_SECTIONS[0])
const tab = computed(() => section.value.key)
const searching = computed(() => Boolean(query.value.trim()))
const results = computed(() => searchSettings(query.value))
const sectionsIn = group => SETTINGS_SECTIONS.filter(item => item.group === group)
const resultCount = key => results.value.filter(item => item.section.key === key).length

function back () {
  if (/^\/(chats|workflows|runs)(\/|$)/.test(window.history.state?.back || '')) router.back()
  else router.push('/chats')
}
function choosePackage (source) { query.value = ''; router.replace({ path: '/settings/packages', query: { source } }) }
function clearSearch () { query.value = ''; searchInput.value?.focus() }
function openSection (key) { query.value = ''; router.replace(`/settings/${key}`) }
function shortcuts (event) {
  if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || event.target.closest('input, textarea, select, [contenteditable="true"]')) return
  event.preventDefault()
  searchInput.value?.focus()
}

let observer
let revealed = ''
function revealTarget () {
  if (!route.hash || searching.value || revealed === `${tab.value}${route.hash}`) return
  let id
  try { id = decodeURIComponent(route.hash.slice(1)) } catch { return }
  const target = document.getElementById(id)
  if (!target) { pane.value?.revealSetting?.(id); return }
  if (!body.value?.contains(target)) return
  for (let parent = target; parent && parent !== body.value; parent = parent.parentElement) {
    if (parent.tagName === 'DETAILS') parent.open = true
  }
  body.value.querySelector('.settings__target')?.classList.remove('settings__target')
  target.classList.add('settings__target')
  if (!target.matches('input, select, textarea, button, a, summary')) target.setAttribute('tabindex', '-1')
  target.focus({ preventScroll: true })
  target.scrollIntoView({ block: 'center', behavior: 'instant' })
  revealed = `${tab.value}${route.hash}`
}
watch([tab, () => route.hash, searching], async () => {
  revealed = ''
  await nextTick()
  if (body.value) body.value.scrollTop = 0
  revealTarget()
})
watch(pane, () => nextTick(revealTarget))
watch(() => route.query.q, value => { query.value = typeof value === 'string' ? value : '' })
onMounted(() => {
  document.addEventListener('keydown', shortcuts)
  // Lazy-loaded panes (and asynchronous forms) can mount after navigation.
  observer = new MutationObserver(revealTarget)
  observer.observe(body.value, { childList: true, subtree: true })
  revealTarget()
})
onUnmounted(() => { document.removeEventListener('keydown', shortcuts); observer?.disconnect() })
</script>

<style scoped>
.settings { min-width: 0; overflow: hidden; }
.settings__head { gap: 14px; }
.settings__breadcrumb { display: flex; align-items: center; gap: 12px; font-size: 13px; color: var(--text-dim); }
.settings__breadcrumb h1 { color: var(--text); font-weight: 600; }
.settings__shortcut { margin-right: 12px; }
.settings__searchbar { padding: 20px 28px; border-bottom: 1px solid var(--border); background: var(--surface-app); }
.settings__search { display: flex; align-items: center; gap: 12px; min-height: 44px; padding: 0 10px 0 14px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface-input); }
.settings__search:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.settings__search > .material-icons { color: var(--text-dim); font-size: 20px; }
.settings__search input { flex: 1; width: 100%; min-width: 0; background: transparent; color: var(--text); border: 0; outline: 0; font: inherit; font-size: 14px; }
.settings__search input::placeholder { color: var(--text-dim); }
.settings__search input::-webkit-search-cancel-button { display: none; }
.settings__result-count { font-size: 11px; white-space: nowrap; color: var(--text-muted); }
.settings__workspace { display: grid; grid-template-columns: 228px minmax(0, 1fr); }
.settings__tree { display: flex; flex-direction: column; min-width: 0; padding: 20px 14px 16px; border-right: 1px solid var(--border); background: var(--surface-panel); }
.settings__group + .settings__group { margin-top: 24px; }
.settings__group h2 { margin: 0 10px 8px; font-size: 10px; letter-spacing: 0.1em; }
.settings__category { display: flex; align-items: center; gap: 10px; min-height: 36px; padding: 8px 10px; margin: 2px 0; border-radius: var(--radius-sm); color: var(--text-muted); text-decoration: none; font-size: 12.5px; }
.settings__category > .material-icons { font-size: 17px; }
.settings__category:hover { background: var(--surface-hover); color: var(--text); }
.settings__category--active { color: var(--accent); background: var(--accent-soft); }
.settings__count { font-size: 10px; color: var(--accent); }
.settings__tree-foot { display: flex; align-items: center; gap: 8px; padding: 28px 10px 0; margin-top: auto; font-size: 10px; color: var(--text-dim); }
.settings__tree-foot .material-icons { font-size: 14px; }
.settings__body { min-width: 0; padding: 28px 40px 40px; scroll-padding: 28px; }
.settings__inner { width: 100%; max-width: 940px; margin: 0 auto; }
.settings__scope-line { display: flex; align-items: center; justify-content: space-between; color: var(--text-dim); font-size: 11px; margin-bottom: 10px; }
.settings__scope { display: inline-flex; align-items: center; flex: none; height: 24px; padding: 0 8px; color: var(--text-muted); border: 1px solid var(--border); border-radius: var(--radius-xs); font-size: 10px; white-space: nowrap; }
.settings__section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 22px; }
.settings__result { display: flex; align-items: center; gap: 16px; padding: 18px 8px; border-bottom: 1px solid var(--border); border-radius: var(--radius-xs); text-decoration: none; color: var(--text); }
.settings__result:hover { background: var(--surface-hover); }
.settings__result-icon { color: var(--text-dim); font-size: 20px; }
.settings__result-path { font-size: 10px; color: var(--text-dim); }
.settings__result h3 { margin: 4px 0; font-weight: 600; font-size: 14px; }
.settings__result p { margin: 0; color: var(--text-muted); font-size: 12px; }
.settings__result-arrow { font-size: 16px; color: var(--accent); }
.settings__no-results { color: var(--text-muted); overflow-wrap: anywhere; }
.settings__mobile-nav { display: none; }
@media (max-width: 1100px) {
  .settings__workspace { grid-template-columns: 208px minmax(0, 1fr); }
  .settings__body { padding: 24px; }
}
@media (max-width: 760px) {
  .settings__workspace { grid-template-columns: minmax(0, 1fr); }
  .settings__tree, .settings__shortcut, .settings__search-key, .settings__breadcrumb > span { display: none; }
  .settings__searchbar { padding: 12px max(16px, env(safe-area-inset-right)) 12px max(16px, env(safe-area-inset-left)); }
  .settings__mobile-nav { display: block; margin-top: 10px; }
  .settings__mobile-nav select { font-size: 13px; }
  .settings__body { padding: 20px max(16px, env(safe-area-inset-right)) max(32px, env(safe-area-inset-bottom)) max(16px, env(safe-area-inset-left)); }
  .settings__head { gap: 8px; }
  .settings__result { gap: 10px; padding: 16px 0; }
  .settings__result .settings__scope, .settings__result-arrow { display: none; }
}
</style>
