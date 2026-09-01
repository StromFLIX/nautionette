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
