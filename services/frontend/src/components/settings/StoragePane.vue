<template>
  <div class="storage-heading">
    <h2 id="storage-overview" class="settings__title">Storage</h2>
    <button type="button" class="btn btn--sm btn--outline" :disabled="loading || !!busy" @click="load">Refresh usage</button>
  </div>
  <p class="settings__intro">Output files, editable sources and safe previews live in private S3-compatible storage. SQLite keeps the chat ownership and revision manifests, not new file bytes.</p>
  <p v-if="error" class="caption field-hint--bad" role="alert">{{ error }} <button type="button" class="btn btn--sm" @click="load">Retry loading</button></p>
  <p v-if="notice" class="caption storage-notice" role="status">{{ notice }}</p>
  <p v-if="loading && !status" class="caption dim" role="status">Loading storage usage…</p>
  <template v-if="status">
    <section class="storage-box" aria-label="Output storage usage">
      <h3>{{ status.provider }}</h3>
      <p class="caption"><strong>{{ status.health.status === 'ok' ? 'Connected' : 'Unavailable' }}</strong> · {{ status.bucket }}</p>
      <p class="caption dim storage-endpoint">{{ status.endpoint }} · {{ status.region }}</p>
      <p v-if="status.health.error" class="caption field-hint--bad" role="alert">{{ message(status.health.error) }}</p>
      <progress class="storage-progress" :value="Math.min(status.usage.charged_bytes, status.policy.global_limit_bytes)" :max="status.policy.global_limit_bytes" aria-label="Output storage quota" />
      <p class="caption"><strong>{{ bytes(status.usage.charged_bytes) }}</strong> charged of <strong>{{ bytes(status.policy.global_limit_bytes) }}</strong> · {{ bytes(Math.max(0, status.policy.global_limit_bytes - status.usage.charged_bytes)) }} quota remaining</p>
      <dl class="storage-stats">
        <div><dt>Retained originals</dt><dd>{{ bytes(status.usage.original_bytes) }}</dd></div>
        <div><dt>Safe previews</dt><dd>{{ bytes(status.usage.preview_bytes) }}</dd></div>
        <div><dt>Outputs / revisions / files</dt><dd>{{ status.usage.output_count }} / {{ status.usage.revision_count }} / {{ status.usage.file_count }}</dd></div>
        <div><dt>Active upload reservations</dt><dd>{{ bytes(status.usage.reserved_bytes) }}</dd></div>
        <div><dt>Awaiting durable object deletion</dt><dd>{{ bytes(status.usage.pending_deletion_bytes) }}</dd></div>
        <div><dt>Temporary / staged objects</dt><dd>{{ bytes(status.usage.temporary_bytes) }}</dd></div>
      </dl>
      <p class="caption dim">Charged usage includes retained files, reservations and removed durable objects awaiting deletion. Temporary transfer copies are separate. Expiring upload URLs delay staging cleanup for up to 11 minutes.</p>
      <p class="caption storage-warning">{{ status.maxio_notice }}</p>
    </section>

    <h3 class="storage-subtitle">System capacity</h3>
    <p class="caption dim">Capacity is measured on the actual mounted filesystems, not inferred from the S3 API. Filesystems can share one host disk: do not add their totals. Increasing an output quota does not create physical disk space.</p>
    <div class="storage-capacity">
      <section v-for="disk in status.filesystems" :key="disk.label" class="storage-box" :aria-label="disk.label">
        <h3>{{ disk.label }}</h3>
        <dl v-if="disk.available" class="storage-stats">
          <div><dt>Total space</dt><dd>{{ bytes(disk.total_bytes) }}</dd></div>
          <div><dt>Used by all system data</dt><dd>{{ bytes(disk.used_bytes) }}</dd></div>
          <div><dt>Available space</dt><dd>{{ bytes(disk.available_bytes) }}</dd></div>
        </dl>
        <p v-else class="caption dim">Capacity unavailable: {{ disk.reason }}.</p>
      </section>
    </div>

    <form class="storage-box" @submit.prevent="save">
      <h3 id="storage-limits">Storage limits</h3>
      <p class="caption dim">Enforced on prepare and commit, including simultaneous uploads. Limits can be lowered below current usage; further publications are blocked until space is freed. These settings do not delete files.</p>
      <div class="storage-fields">
        <label v-for="field in quotaFields" :key="field.key" class="caption" :for="`storage-${field.key}`">{{ field.label }} ({{ field.unit }})
          <input :id="`storage-${field.key}`" v-model.number="draft[field.key]" class="field" type="number" :min="field.min" :max="field.max" :step="field.step || 1" required :disabled="!!busy" />
        </label>
      </div>
      <p class="caption dim">Protocol safety ceilings: 1 GiB per file, 2 GiB per publication, 8 files per snapshot, 50 outputs per chat and 64 revision numbers per output. Deleted revision numbers are never reused.</p>
      <h3 id="storage-retention">Automatic retention</h3>
      <label class="storage-check caption"><input v-model="retentionEnabled" type="checkbox" :disabled="!!busy" /> Enable automatic permanent cleanup</label>
      <div class="storage-retention" v-if="retentionEnabled">
        <label class="caption" for="storage-retention-days">Remove revisions older than (days)
          <input id="storage-retention-days" v-model.number="retentionDays" class="field" type="number" min="1" max="36500" step="1" required :disabled="!!busy" />
        </label>
        <label class="storage-check caption"><input v-model="keepLatest" type="checkbox" :disabled="!!busy" /> Keep each output's current revision</label>
        <p class="caption storage-warning">Once saved, matching revisions will be permanently removed by background maintenance. Age is measured from each revision's publication, not when you last viewed it. Back up anything you need first.</p>
      </div>
      <p v-else class="caption dim">Off by default. No age-based cleanup is running.</p>
      <button type="submit" class="btn btn--primary" :disabled="!!busy">{{ busy === 'save' ? 'Saving…' : 'Save storage settings' }}</button>
    </form>

    <section class="storage-box" aria-label="Migration and maintenance">
      <h3>Migration &amp; maintenance</h3>
      <p class="caption">Legacy files still in SQLite: <strong>{{ status.usage.legacy_file_count }}</strong>. Each original and preview is copied and read back with checksum verification before its database bytes are released. Failed migrations retain the original data and retry.</p>
      <p class="caption">SQLite and WAL: {{ bytes(status.database.bytes) }} · Reusable pages: {{ bytes(status.database.reclaimable_bytes) }}.</p>
      <p class="caption dim">Freed pages are reclaimed automatically after migration when no chat turn is running. Manual compaction needs temporary free space and can briefly block metadata writes. This does not alter incoming chat attachments.</p>
      <p v-for="(failure, index) in [...(status.maintenance.errors || []), ...cleanupErrors]" :key="index" class="caption field-hint--bad" role="alert">{{ message(failure) }}</p>
      <div class="storage-actions">
        <button type="button" class="btn btn--sm btn--outline" :disabled="!!busy" @click="maintain">Retry migration &amp; cleanup</button>
        <button type="button" class="btn btn--sm btn--outline" :disabled="busy || !status.database.reclaimable_bytes" @click="compact">Reclaim database space</button>
      </div>
    </section>
  </template>

  <h3 id="storage-outputs" class="storage-subtitle">Stored outputs</h3>
  <p class="caption dim">Every stored revision, including historical snapshots. Sort largest first to find big files. Deleting a whole output removes all its revisions; deleting its current revision falls back to the newest retained snapshot.</p>
  <form class="storage-filters" @submit.prevent="offset = 0; loadInventory()">
    <label class="caption grow" for="storage-search">Search outputs<input id="storage-search" v-model="search" class="field" placeholder="Chat, output title or filename" maxlength="200" /></label>
    <label class="caption" for="storage-sort">Sort outputs<select id="storage-sort" v-model="sort" class="field" @change="offset = 0; loadInventory()"><option value="largest">Largest first</option><option value="oldest">Oldest first</option><option value="newest">Newest first</option></select></label>
    <button type="submit" class="btn btn--sm btn--outline">Search</button>
  </form>
  <p v-if="inventoryError" class="caption field-hint--bad" role="alert">{{ inventoryError }} <button type="button" class="btn btn--sm" @click="loadInventory">Retry inventory</button></p>
  <p v-if="inventoryLoading" class="caption dim" role="status">Loading outputs…</p>
  <div class="storage-actions">
    <label class="storage-check caption"><input type="checkbox" :checked="allSelected" :disabled="!rows.length" @change="selectPage($event.target.checked)" /> Select this page</label>
    <button type="button" class="btn btn--sm btn--outline" :disabled="busy || !selected.length" @click="preview({ revisions: selected.map(revisionSelection) })">Delete selected revisions ({{ selected.length }})</button>
    <button v-if="selected.length" type="button" class="btn btn--sm" @click="selected = []">Clear selection</button>
  </div>
  <p v-if="!rows.length && !inventoryLoading && !inventoryError" class="caption dim">No stored outputs match.</p>
  <article v-for="row in rows" :key="revisionKey(row)" class="storage-output" :aria-label="`${row.title}, revision ${row.revision}`">
    <label class="storage-check"><input v-model="selected" type="checkbox" :value="revisionKey(row)" :aria-label="`Select ${row.title} revision ${row.revision}`" /></label>
    <div class="grow">
      <h4>{{ row.title }} <span class="caption dim">· Revision {{ row.revision }}{{ row.is_current ? ' · Current' : ' · Historical' }}</span></h4>
      <p class="caption dim"><RouterLink :to="`/chats/${row.chat_id}`">{{ row.chat_title }}</RouterLink> · {{ date(row.created_at) }} · {{ row.file_count }} files</p>
      <p class="caption storage-filenames">{{ row.files.map(file => `${file.name} (${bytes(file.size)})`).join(' · ') }}</p>
    </div>
    <div class="storage-output__size"><strong>{{ bytes(row.total_bytes) }}</strong><p class="caption dim">incl. previews</p><button type="button" class="btn btn--sm" :disabled="!!busy" @click="preview({ output_ids: [row.output_id] })">Delete whole output</button></div>
  </article>
  <div class="storage-actions storage-pages">
    <span class="caption dim">{{ total ? `${offset + 1}–${Math.min(offset + rows.length, total)} of ${total} revisions` : '0 revisions' }}</span>
    <button type="button" class="btn btn--sm" :disabled="!offset || inventoryLoading" @click="offset = Math.max(0, offset - 50); loadInventory()">Previous page</button>
    <button type="button" class="btn btn--sm" :disabled="offset + rows.length >= total || inventoryLoading" @click="offset += 50; loadInventory()">Next page</button>
  </div>
  <section class="storage-box" aria-label="Age-based cleanup">
    <h3>Remove old outputs</h3>
    <p class="caption dim">Manual cleanup always previews the exact affected revisions and bytes before deletion. It does not change automatic retention.</p>
    <form class="storage-filters" @submit.prevent="preview({ older_than_days: ageDays, keep_latest: ageKeepLatest })">
      <label class="caption" for="storage-cleanup-days">Older than (days)<input id="storage-cleanup-days" v-model.number="ageDays" class="field" type="number" min="0" max="36500" step="1" required /></label>
      <label class="storage-check caption"><input v-model="ageKeepLatest" type="checkbox" /> Keep current revisions</label>
      <button type="submit" class="btn btn--sm btn--outline" :disabled="!!busy">Preview age cleanup</button>
    </form>
  </section>
  <q-dialog v-model="showPlan" :persistent="busy === 'delete'">
    <div class="storage-confirm" role="document" aria-labelledby="storage-confirm-title">
      <h3 id="storage-confirm-title">Review permanent output deletion</h3>
      <template v-if="plan">
        <p>{{ plan.revision_count }} revisions from {{ plan.output_count }} outputs · {{ bytes(plan.bytes) }} retained file data.</p>
        <p v-if="plan.includes_current" class="storage-warning">This includes current revisions. An output with no revisions left will disappear. Existing reply cards are removed, not left with broken downloads.</p>
        <p class="caption dim">Only this reviewed snapshot is deleted. Newer publications are never silently added. Object deletion retries if storage is unavailable; quota is released after deletion succeeds.</p>
        <ul><li v-for="item in plan.revisions.slice(0, 10)" :key="revisionKey(item)">{{ item.title }} · Revision {{ item.revision }} · {{ bytes(item.total_bytes) }}</li></ul>
        <p v-if="plan.revision_count > 10" class="caption dim">And {{ plan.revision_count - 10 }} more revisions.</p>
        <p v-if="deleteError" class="caption field-hint--bad" role="alert">{{ deleteError }}</p>
        <div class="storage-actions"><button type="button" class="btn btn--outline" :disabled="busy === 'delete'" @click="showPlan = false">Cancel</button><button type="button" class="btn btn--primary" :disabled="busy === 'delete' || !plan.revision_count" @click="remove">{{ busy === 'delete' ? 'Deleting…' : 'Permanently delete' }}</button></div>
      </template>
    </div>
  </q-dialog>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '../../api'
import { onLiveEvent } from '../../store'

const MiB = 1024 * 1024, GiB = 1024 * MiB
const quotaFields = [
  { key: 'global_limit_bytes', label: 'Instance output quota', unit: 'GiB', scale: GiB, min: 0.001, step: 'any' },
  { key: 'chat_limit_bytes', label: 'Per-chat output quota', unit: 'MiB', scale: MiB, min: 0.001, step: 'any' },
  { key: 'max_file_bytes', label: 'Maximum original file size', unit: 'MiB', scale: MiB, min: 0.001, max: 1024, step: 'any' },
  { key: 'max_publication_bytes', label: 'Maximum publication size', unit: 'MiB', scale: MiB, min: 0.001, max: 2048, step: 'any' },
  { key: 'minimum_free_bytes', label: 'Minimum free filesystem reserve', unit: 'MiB', scale: MiB, min: 0, step: 'any' }
]
const status = ref(null), loading = ref(true), busy = ref(''), error = ref(''), notice = ref('')
const draft = ref({}), retentionEnabled = ref(false), retentionDays = ref(30), keepLatest = ref(true)
const rows = ref([]), total = ref(0), offset = ref(0), sort = ref('largest'), search = ref(''), selected = ref([])
const inventoryLoading = ref(false), inventoryError = ref(''), ageDays = ref(30), ageKeepLatest = ref(true)
const plan = ref(null), showPlan = ref(false), deleteError = ref(''), cleanupErrors = ref([])
let disposed = false, controller, generation = 0, timer, unsubscribe
const revisionKey = item => `${item.output_id}:${item.revision}`
const revisionSelection = value => { const [output_id, revision] = value.split(':'); return { output_id, revision: Number(revision) } }
const allSelected = computed(() => rows.value.length > 0 && rows.value.every(row => selected.value.includes(revisionKey(row))))
const message = failure => typeof failure === 'string' ? failure : failure?.message || failure?.code || 'Storage operation failed'
function bytes (value = 0) { if (!value) return '0 B'; const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; const i = Math.min(4, Math.floor(Math.log(value) / Math.log(1024))); return `${(value / 1024 ** i).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${units[i]}` }
const date = value => new Date(value * 1000).toLocaleString()
function syncDraft () {
  for (const field of quotaFields) draft.value[field.key] = status.value.policy[field.key] / field.scale
  retentionEnabled.value = status.value.policy.retention_days > 0
  retentionDays.value = status.value.policy.retention_days || 30
  keepLatest.value = status.value.policy.retention_keep_latest
}
async function loadStatus () {
  try { const data = await api.outputStorage(); if (!disposed) { const initial = !status.value; status.value = data; if (initial) syncDraft(); error.value = '' } }
  catch (failure) { if (!disposed) error.value = failure.message }
}
async function loadInventory () {
  controller?.abort(); controller = new AbortController(); const current = ++generation
  inventoryLoading.value = true
  try {
    const data = await api.storedOutputs({ offset: offset.value, limit: 50, sort: sort.value, query: search.value }, controller.signal)
    if (!disposed && current === generation) { rows.value = data.outputs; total.value = data.total; inventoryError.value = '' }
  } catch (failure) { if (!disposed && current === generation && failure.name !== 'AbortError') inventoryError.value = failure.message }
  finally { if (!disposed && current === generation) inventoryLoading.value = false }
}
async function load () { loading.value = true; await Promise.all([loadStatus(), loadInventory()]); if (!disposed) loading.value = false }
function selectPage (checked) { const keys = rows.value.map(revisionKey); selected.value = checked ? [...new Set([...selected.value, ...keys])] : selected.value.filter(key => !keys.includes(key)) }
async function save () {
  busy.value = 'save'; error.value = ''; notice.value = ''
  try {
    const changes = Object.fromEntries(quotaFields.map(field => [field.key, Math.round(draft.value[field.key] * field.scale)]))
    await api.saveOutputStorage({ ...changes, retention_days: retentionEnabled.value ? retentionDays.value : 0, retention_keep_latest: keepLatest.value })
    notice.value = 'Storage settings saved. Quotas apply to new publications immediately.'
    await loadStatus(); syncDraft()
  } catch (failure) { error.value = failure.message }
  finally { busy.value = '' }
}
async function preview (selector) {
  busy.value = 'preview'; error.value = ''; deleteError.value = ''; notice.value = ''
  try { const data = await api.previewOutputCleanup(selector); if (!disposed) { plan.value = data; showPlan.value = true } }
  catch (failure) { error.value = failure.message }
  finally { busy.value = '' }
}
async function remove () {
  busy.value = 'delete'; deleteError.value = ''
  try {
    const result = await api.deleteStoredOutputs(plan.value.plan_id)
    cleanupErrors.value = result.collection.errors
    showPlan.value = false; selected.value = []; offset.value = 0
    notice.value = `Removed ${result.deleted_revisions} revisions. ${bytes(result.collection.reclaimed_bytes)} reclaimed now.${result.collection.errors.length ? ' Object deletion is pending and will retry; check maintenance errors below.' : ''}`
    await load()
  } catch (failure) { deleteError.value = failure.message }
  finally { busy.value = '' }
}
async function maintain () {
  busy.value = 'maintenance'; error.value = ''; notice.value = ''
  try { const result = await api.maintainOutputStorage(); cleanupErrors.value = []; notice.value = `Maintenance finished: ${result.migrated_files} files migrated; ${result.deleted_revisions} revisions removed by enabled retention.${result.errors.length ? ' Some operations need attention; see the errors below.' : ''}`; await load() }
  catch (failure) { error.value = failure.message }
  finally { busy.value = '' }
}
async function compact () {
  busy.value = 'compact'; error.value = ''; notice.value = ''
  try { await api.compactOutputDatabase(); notice.value = 'Database compaction completed.'; await loadStatus() }
  catch (failure) { error.value = failure.message }
  finally { busy.value = '' }
}
onMounted(() => {
  load()
  unsubscribe = onLiveEvent(event => { if (event.kind?.startsWith('chat.output.') || event.kind === 'settings.changed') { clearTimeout(timer); timer = setTimeout(load, 250) } })
})
onUnmounted(() => { disposed = true; controller?.abort(); clearTimeout(timer); unsubscribe?.() })
</script>

<style scoped>
.storage-heading, .storage-actions, .storage-filters { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.storage-heading { justify-content: space-between; margin-bottom: 12px; }
.storage-heading h2 { margin: 0; }
.storage-box { margin: 18px 0; padding: 18px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-panel); overflow-wrap: anywhere; }
.storage-box h3, .storage-output h4 { margin: 0 0 10px; font-size: 0.9375rem; }
.storage-box p { line-height: 1.7; }
.storage-subtitle { margin: 28px 0 10px; font-size: 1rem; }
.storage-warning, .storage-notice { padding: 12px; border-radius: var(--radius-sm); background: var(--warning-soft); line-height: 1.7; }
.storage-endpoint, .storage-filenames { overflow-wrap: anywhere; }
.storage-progress { width: 100%; height: 12px; accent-color: var(--accent); }
.storage-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 18px; margin: 20px 0; }
.storage-stats dt { font-size: 0.75rem; color: var(--text-muted); }
.storage-stats dd { margin: 7px 0 0; font-size: 0.9375rem; font-weight: 600; }
.storage-capacity { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr)); gap: 14px; }
.storage-capacity .storage-box { margin: 0; }
.storage-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr)); gap: 16px; margin: 18px 0; }
.storage-fields label, .storage-filters > label:not(.storage-check), .storage-retention > label:not(.storage-check) { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.storage-retention { margin: 16px 0; }
.storage-retention .field { max-width: 150px; }
.storage-check { display: flex; align-items: center; gap: 8px; }
.storage-check input { accent-color: var(--accent); }
#storage-retention { margin-top: 24px; }
.storage-retention .storage-check { margin-top: 16px; }
.storage-filters { align-items: flex-end; margin: 18px 0; }
.storage-filters .field { min-width: 0; max-width: 100%; }
.storage-filters .grow { flex: 1; min-width: min(220px, 100%); }
.storage-output { display: flex; align-items: flex-start; gap: 14px; padding: 18px 0; border-bottom: 1px solid var(--border); overflow-wrap: anywhere; }
.storage-output > .grow { min-width: 0; }
.storage-output h4 { line-height: 1.6; margin-bottom: 6px; }
.storage-output p { margin: 6px 0; line-height: 1.6; }
.storage-output__size { text-align: right; min-width: 110px; }
.storage-output__size .btn { white-space: normal; }
.storage-pages { justify-content: flex-end; margin-top: 16px; }
.storage-confirm { max-width: 540px; width: 100%; padding: 24px; background: var(--surface-panel); color: var(--text); border-radius: var(--radius-md); overflow-wrap: anywhere; }
.storage-confirm p, .storage-confirm li { line-height: 1.7; }
.storage-confirm .storage-actions { justify-content: flex-end; margin-top: 20px; }
@media (max-width: 640px) { .storage-box { padding: 14px; } .storage-output { flex-wrap: wrap; } .storage-output > .grow { flex-basis: calc(100% - 40px); } .storage-output__size { width: 100%; display: flex; align-items: center; justify-content: flex-end; gap: 10px; } .storage-confirm { padding: 18px; } }
</style>
