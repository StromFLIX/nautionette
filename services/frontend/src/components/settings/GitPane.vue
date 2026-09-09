<template>
  <h2 class="settings__title">Git authorship</h2>
  <p class="settings__intro">Attribution for new commits. Existing history and permissions stay unchanged.</p>

  <form v-if="loaded" @submit.prevent="save(false)">
    <div class="setting">
      <label for="git-mode" class="setting__label">Attribution</label>
      <select id="git-mode" v-model="form.git_authorship_mode" class="field">
        <option value="automation">Nautionette only (current default)</option>
        <option value="human_author">You as author · automation as committer</option>
        <option value="human_author_bot_coauthor">You as author · automation as co-author (recommended)</option>
        <option value="bot_author_human_coauthor">Automation as author · you as co-author</option>
      </select>
      <p class="caption dim">The committer records who created the commit. Co-authors appear in the commit message.</p>
    </div>

    <div class="setting">
      <label for="git-human-name" class="setting__label">Your name</label>
      <input id="git-human-name" v-model="form.git_human_name" class="field" autocomplete="name"
        :required="needsHuman" maxlength="200" placeholder="Your name" />
    </div>
    <div class="setting">
      <label for="git-human-email" class="setting__label">Your Git email</label>
      <input id="git-human-email" v-model="form.git_human_email" class="field" type="email"
        :required="needsHuman" maxlength="254" placeholder="Your GitHub-associated email" />
      <p class="caption dim">
        Commit emails are visible in repository history. For privacy, use the exact noreply address from
        your GitHub email settings. Only attribute someone who contributed or directed the work.
      </p>
    </div>
    <div class="setting">
      <label for="git-bot-name" class="setting__label">Automation name</label>
      <input id="git-bot-name" v-model="form.git_automation_name" class="field" required maxlength="200" />
    </div>
    <div class="setting">
      <label for="git-bot-email" class="setting__label">Automation Git email</label>
      <input id="git-bot-email" v-model="form.git_automation_email" class="field" type="email" required maxlength="254" />
      <p class="caption dim">
        GitHub links authors and co-authors to profiles only when their email matches a GitHub account.
        The default automation email is a label, not a guarantee of a linked bot profile.
      </p>
    </div>

    <div id="commit-preview" class="setting">
      <div class="setting__label">Commit preview</div>
      <pre class="git-preview">{{ preview }}</pre>
    </div>
    <div class="row">
      <span class="caption dim grow">Instance-wide settings · applies on the next agent call</span>
      <button class="btn" type="button" :disabled="saving" @click="save(true)">Reset</button>
      <button class="btn btn--primary" type="submit" :disabled="saving">{{ saving ? 'Saving…' : 'Save' }}</button>
    </div>
  </form>
  <p v-else class="caption dim">{{ loadError || 'Loading…' }}</p>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { api } from '../../api'
import { gitAuthorshipPreview } from '../../git-authorship'

const $q = useQuasar()
const loaded = ref(false)
const loadError = ref('')
const saving = ref(false)
const form = reactive({
  git_authorship_mode: 'automation',
  git_human_name: '',
  git_human_email: '',
  git_automation_name: 'Nautionette',
  git_automation_email: 'nautionette@users.noreply.github.com'
})
const needsHuman = computed(() => form.git_authorship_mode !== 'automation')
const preview = computed(() => gitAuthorshipPreview(form))

function apply (data) {
  for (const key of Object.keys(form)) form[key] = data.settings[key]
}

async function save (reset) {
  saving.value = true
  try {
    const payload = reset ? Object.fromEntries(Object.keys(form).map((key) => [key, null])) : { ...form }
    apply(await api.saveSettings(payload))
    $q.notify({ type: 'positive', message: 'Git authorship saved. Applies on the next agent call.' })
  } catch (error) {
    $q.notify({ type: 'negative', message: error.message })
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    apply(await api.settings())
    loaded.value = true
  } catch (error) {
    loadError.value = error.message
  }
})
</script>

<style scoped>
.git-preview {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 0.75rem;
}
</style>
