import assert from 'node:assert/strict'
import { test } from 'node:test'
import { gitAuthorshipPreview } from './git-authorship.js'

const settings = {
  git_human_name: ' Jane ', git_human_email: 'jane@example.test',
  git_automation_name: 'Bot', git_automation_email: 'bot@example.test'
}

for (const [mode, author, coauthor] of [
  ['automation', 'Bot <bot@example.test>', null],
  ['human_author', 'Jane <jane@example.test>', null],
  ['human_author_bot_coauthor', 'Jane <jane@example.test>', 'Bot <bot@example.test>'],
  ['bot_author_human_coauthor', 'Bot <bot@example.test>', 'Jane <jane@example.test>']
]) {
  test(`preview represents ${mode}`, () => {
    const preview = gitAuthorshipPreview({ ...settings, git_authorship_mode: mode })
    assert.ok(preview.startsWith(`Author: ${author}\nCommitter: Bot <bot@example.test>\n`))
    if (coauthor) assert.ok(preview.endsWith(`Co-authored-by: ${coauthor}`))
    else assert.doesNotMatch(preview, /Co-authored-by:/)
    assert.doesNotMatch(preview, /Signed-off-by:/)
  })
}

test('empty human fields show placeholders, not an invented identity', () => {
  const preview = gitAuthorshipPreview({ ...settings, git_authorship_mode: 'human_author', git_human_name: '', git_human_email: '' })
  assert.match(preview, /Author: Your name <your Git email>/)
})
