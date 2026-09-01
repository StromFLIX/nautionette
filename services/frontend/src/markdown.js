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
  return DOMPurify.sanitize(marked.parse(text || ''), {
    ALLOWED_TAGS: [
      'p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'blockquote', 'a', 'span',
      'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'hr',
      'table', 'thead', 'tbody', 'tr', 'th', 'td'
    ],
    ALLOWED_ATTR: ['href', 'title', 'class'],
    ALLOWED_URI_REGEXP: /^https?:|^mailto:/i,
    ADD_ATTR: ['target', 'rel']
  })
}

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})
