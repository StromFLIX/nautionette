import { loadDownloads } from './releases.mjs'
import { setupDefinition } from './definitions.mjs'
import { mountActivity } from './activity.mjs?v=living-3'
import { appURL } from './urls.mjs'

// Storage is optional: blocked cookies/storage must not break the page.
let storage
try { storage = window.localStorage } catch { /* Private/embedded browser. */ }
setupDefinition(document, storage)
document.querySelector('.locale-control').hidden = false
mountActivity(document.getElementById('activity-field'), document.getElementById('motion-toggle'), storage)
loadDownloads(document)

for (const button of document.querySelectorAll('[data-copy]')) {
  button.hidden = false
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copy)
      button.textContent = 'Copied'
    } catch {
      button.textContent = 'Select the command below'
    }
    button.setAttribute('role', 'status')
    setTimeout(() => { button.textContent = 'Copy command'; button.removeAttribute('role') }, 2400)
  })
}

function showStaging () {
  if (document.getElementById('staging-banner')) return
  const banner = document.createElement('div')
  banner.id = 'staging-banner'
  banner.textContent = 'Staging · independent copy of production'
  banner.setAttribute('role', 'status')
  document.body.prepend(banner)
}
if (/(^|\.)(stage|staging)(\.|$)/i.test(location.hostname)) showStaging()
fetch('/environment.txt', { cache: 'no-store' })
  .then(response => response.ok ? response.text() : '')
  .then(environment => { if (environment.trim() === 'staging') showStaging() })
  .catch(() => {})

// The existing deployment contract is unchanged; never invent an instance URL.
fetch('/app-url.txt', { cache: 'no-store' })
  .then(response => response.ok ? response.text() : '')
  .then(value => {
    const target = appURL(value)
    if (!target) return
    const link = document.getElementById('app-link')
    link.href = target
    link.textContent = 'Open your instance'
    link.target = '_blank'
    link.rel = 'noopener'
  })
  .catch(() => {})
