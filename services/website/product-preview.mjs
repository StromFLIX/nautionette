// Progressive enhancement: real screenshots remain usable if this optional demo
// cannot load. The example module is fetched only after a visitor chooses it.
export function setupProductPreview (document, loadDemo = () => import('./workspace-demo.mjs?v=explorer-2')) {
  const root = document.querySelector('[data-product-explorer]')
  if (!root) return null
  const toolbar = root.querySelector('[data-explorer-toolbar]')
  const tabs = [...root.querySelectorAll('[data-preview-mode]')]
  const screen = root.querySelector('[data-preview-screen]')
  const demo = root.querySelector('[data-preview-demo]')
  const host = root.querySelector('[data-workspace-demo]')
  const feedback = root.querySelector('[data-preview-feedback]')
  const disclosure = root.querySelector('[data-preview-disclosure]')
  let requested = 'screen'
  let mounted
  let loading
  let destroyed = false

  function show (mode) {
    screen.hidden = mode !== 'screen'
    demo.hidden = mode !== 'demo'
    for (const tab of tabs) {
      const selected = tab.dataset.previewMode === mode
      tab.setAttribute('aria-selected', String(selected))
      tab.tabIndex = selected ? 0 : -1
    }
    disclosure.textContent = mode === 'demo'
      ? 'Interactive example. Sample data only; nothing is sent or saved.'
      : 'Actual app interface, shown with sample content.'
  }

  async function activate (mode) {
    requested = mode
    if (mode === 'screen') { show('screen'); return }
    if (!mounted) {
      feedback.hidden = false
      feedback.textContent = 'Loading the local example…'
      const tab = tabs.find(tab => tab.dataset.previewMode === 'demo')
      tab.setAttribute('aria-busy', 'true')
      try {
        loading ||= loadDemo()
        const module = await loading
        if (destroyed) return
        mounted ||= module.mountWorkspaceDemo(host)
        feedback.hidden = true
        feedback.textContent = ''
      } catch {
        loading = null
        if (destroyed) return
        host.replaceChildren()
        show('screen')
        feedback.hidden = false
        feedback.textContent = 'The demo couldn’t load. The real screenshots are still available.'
        return
      } finally {
        tab.removeAttribute('aria-busy')
      }
    }
    if (!destroyed && requested === 'demo') show('demo')
  }

  function click (event) {
    const tab = event.target.closest('[data-preview-mode]')
    if (tab && root.contains(tab)) activate(tab.dataset.previewMode)
  }
  function keydown (event) {
    const index = tabs.indexOf(event.target)
    if (index < 0) return
    let next
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
    else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = tabs.length - 1
    if (next !== undefined) { event.preventDefault(); tabs[next].focus() }
  }

  root.classList.add('is-enhanced')
  toolbar.hidden = false
  disclosure.hidden = false
  root.addEventListener('click', click)
  root.addEventListener('keydown', keydown)
  show('screen')
  return {
    destroy () {
      destroyed = true
      mounted?.destroy()
      root.removeEventListener('click', click)
      root.removeEventListener('keydown', keydown)
      root.classList.remove('is-enhanced')
      toolbar.hidden = true
      disclosure.hidden = true
      feedback.hidden = true
      show('screen')
    },
  }
}
