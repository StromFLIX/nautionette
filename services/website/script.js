// Reveal on scroll, copy buttons, and an "Open the app" link that points at the
// running instance when the deployment tells us where it is.
document.addEventListener('DOMContentLoaded', () => {
  const revealables = document.querySelectorAll('.section, .strip, .cta, .hero-demo')
  revealables.forEach((element) => element.classList.add('reveal'))

  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible')
        observer.unobserve(entry.target)
      }
    })
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 })

  revealables.forEach((element) => observer.observe(element))

  document.querySelectorAll('.copy').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copy || '')
        const original = button.textContent
        button.textContent = 'Copied'
        setTimeout(() => { button.textContent = original }, 1600)
      } catch {
        button.textContent = 'Press ⌘C'
      }
    })
  })

  function showStaging () {
    if (document.getElementById('staging-banner')) return
    const banner = document.createElement('div')
    banner.id = 'staging-banner'
    banner.textContent = 'STAGING — independent copy of production'
    banner.setAttribute('role', 'status')
    banner.style.cssText = 'position:sticky;top:0;z-index:1000;padding:10px;text-align:center;background:#ffcf5c;color:#302000;font-weight:700'
    document.body.prepend(banner)
  }
  if (/(^|\.)(stage|staging)(\.|$)/i.test(location.hostname)) showStaging()
  fetch('/environment.txt', { cache: 'no-store' })
    .then((response) => response.ok ? response.text() : '')
    .then((environment) => { if (environment.trim() === 'staging') showStaging() })
    .catch(() => {})

  // APP_URL is injected at container start; without it the buttons scroll to self-host.
  fetch('/app-url.txt', { cache: 'no-store' })
    .then((response) => (response.ok ? response.text() : ''))
    .then((url) => {
      const target = url.trim()
      if (!target || target.startsWith('#')) return
      document.querySelectorAll('#app-link, #app-link-2').forEach((link) => {
        link.href = target
        link.target = '_blank'
        link.rel = 'noopener'
      })
    })
    .catch(() => {})
})
