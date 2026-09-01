import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import javascript from 'highlight.js/lib/languages/javascript'
import jsonLang from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import './styles/code.css'

hljs.registerLanguage('python', python)
hljs.registerLanguage('json', jsonLang)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('javascript', javascript)

// highlight.js escapes what it does not recognise, so the result is safe to bind.
export function highlight (code, language = 'python') {
  if (!code) return ''
  if (!hljs.getLanguage(language)) return hljs.highlightAuto(code).value
  return hljs.highlight(code, { language, ignoreIllegals: true }).value
}
