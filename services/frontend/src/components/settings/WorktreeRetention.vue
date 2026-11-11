<template>
  <section id="worktree-retention" class="setting">
    <h3 class="setting__label">Worktree retention</h3>
    <p class="caption dim">Remove entire inactive per-chat worktrees, without scanning dependency directories. Chats, attachments, outputs and shared repository clones are kept. A worktree is recreated when the chat next uses its project.</p>
    <p v-if="error" role="alert" class="retention-error">{{ error }}</p>
    <form v-if="loaded" class="retention-form" @submit.prevent="save">
      <label><input v-model="form.worktree_cleanup_enabled" type="checkbox" /> Enable automatic worktree cleanup</label>
      <label><input v-model="form.worktree_cleanup_clean_enabled" type="checkbox" /> Delete clean, pushed worktrees</label>
      <label>Clean, pushed retention (days)
        <input v-model.number="form.worktree_cleanup_clean_days" class="field" type="number" min="1" max="36500" step="1" required />
      </label>
      <label><input v-model="form.worktree_cleanup_changed_enabled" type="checkbox" /> Delete worktrees with uncommitted or unpushed changes</label>
      <label>Changed or unpushed retention (days)
        <input v-model.number="form.worktree_cleanup_changed_days" class="field" type="number" min="1" max="36500" step="1" required />
      </label>
      <p class="caption retention-warning">Warning: changed-worktree cleanup permanently discards local edits and unpublished work. Clean-worktree cleanup also removes ignored files. Publication is checked against local origin tracking refs, not the chat's initial revision.</p>
      <label>Check interval (hours)
        <input v-model.number="form.worktree_cleanup_interval_hours" class="field" type="number" min="1" max="168" step="1" required />
      </label>
      <label>Maximum worktrees checked per pass
        <input v-model.number="form.worktree_cleanup_batch_size" class="field" type="number" min="1" max="1000" step="1" required />
      </label>
      <p class="caption dim">Age is measured from last worktree use, not creation. Active agents, queued turns and branch snapshots are protected. Settings apply without a redeploy. Checks rotate through larger collections using the configured batch size.</p>
      <div class="row retention-actions">
        <button class="btn btn--primary btn--sm" :disabled="busy">Save worktree retention</button>
        <button type="button" class="btn btn--sm" :disabled="busy" @click="preview">Preview saved policy</button>
      </div>
    </form>
    <p v-else class="caption dim" role="status">Loading worktree retention…</p>
    <p v-if="saved" class="caption" role="status">Worktree retention saved.</p>
    <p v-if="lastRun" class="caption dim" role="status">Last check: {{ new Date(lastRun.at * 1000).toLocaleString() }}. {{ lastRun.error || `${lastRun.checked || 0} checked, ${lastRun.deleted || 0} deleted, ${lastRun.active || 0} active, ${lastRun.errors || 0} errors.` }}</p>
    <p v-if="report" class="caption" role="status">Preview: {{ report.checked }} checked, {{ report.eligible }} eligible, {{ report.active }} active, {{ report.errors }} errors. No files deleted. {{ report.disabled ? 'Automatic cleanup is disabled.' : '' }}</p>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../../api'

const form = reactive({})
const loaded = ref(false)
const busy = ref(false)
const error = ref('')
const saved = ref(false)
const lastRun = ref(null)
const report = ref(null)

onMounted(async () => {
  try {
    const data = await api.worktreeRetention()
    Object.assign(form, data.policy)
    lastRun.value = data.last_run
    loaded.value = true
  } catch (failure) { error.value = failure.message }
})

async function save () {
  busy.value = true
  error.value = ''
  saved.value = false
  try {
    const data = await api.saveSettings({ ...form })
    for (const key of Object.keys(form)) form[key] = data.settings[key]
    saved.value = true
    report.value = null
  } catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}

async function preview () {
  busy.value = true
  error.value = ''
  try { report.value = await api.previewWorktreeCleanup() }
  catch (failure) { error.value = failure.message }
  finally { busy.value = false }
}
</script>

<style scoped>
.retention-form { display: grid; gap: 12px; max-width: 680px; }
.retention-form label { display: grid; gap: 6px; }
.retention-form label:has(input[type="checkbox"]) { display: block; }
.retention-form input[type="number"] { max-width: 180px; }
.retention-actions { gap: 8px; flex-wrap: wrap; }
.retention-warning { color: var(--color-warning, #b56b00); }
.retention-error { color: var(--color-negative, #c33); }
</style>
