import DOMPurify from 'dompurify'
import { Marked } from 'marked'
import { highlight } from './highlight'

const marked = new Marked({ gfm: true, breaks: true })

marked.use({
  renderer: {
    code ({ text, lang }) {
      const language = (lang || '').trim().split(/\s+/)[0]
      return `<pre><code class="hljs">${highlight(text, language || 'json')}</code></pre>`
    }
  }
})

// Anything the agent writes is untrusted, so it is parsed then scrubbed.
export function renderMarkdown (text) {
  const fragment = DOMPurify.sanitize(marked.parse(text || ''), {
    RETURN_DOM_FRAGMENT: true,
    ALLOWED_TAGS: [
      'p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote', 'a', 'span',
      'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'hr',
      'table', 'thead', 'tbody', 'tr', 'th', 'td'
    ],
    ALLOWED_ATTR: ['href', 'title', 'class'],
    ALLOWED_URI_REGEXP: /^https?:|^mailto:/i,
    ADD_ATTR: ['target', 'rel']
  })

  // Add trusted controls only after sanitizing the model's HTML. Keeping the
  // toolbar outside <pre> leaves it visible when long code scrolls sideways.
  for (const pre of fragment.querySelectorAll('pre')) {
    if (!pre.querySelector('code')) continue
    const block = document.createElement('div')
    block.className = 'code-block'
    const toolbar = document.createElement('div')
    toolbar.className = 'code-block__toolbar'
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'btn btn--sm code-block__copy'
    button.textContent = 'Copy code'
    toolbar.append(button)
    pre.replaceWith(block)
    block.append(toolbar, pre)
  }
  const container = document.createElement('div')
  container.append(fragment)
  return container.innerHTML
}

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})
