export function gitAuthorshipPreview (settings) {
  const human = `${settings.git_human_name?.trim() || 'Your name'} <${settings.git_human_email?.trim() || 'your Git email'}>`
  const automation = `${settings.git_automation_name?.trim() || 'Automation name'} <${settings.git_automation_email?.trim() || 'automation Git email'}>`
  const mode = settings.git_authorship_mode
  const author = mode.startsWith('human_author') ? human : automation
  const coauthor = mode === 'human_author_bot_coauthor' ? automation
    : mode === 'bot_author_human_coauthor' ? human : null
  return `Author: ${author}\nCommitter: ${automation}\n\nExample commit message${coauthor ? `\n\nCo-authored-by: ${coauthor}` : ''}`
}
