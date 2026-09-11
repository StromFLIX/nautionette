import generate from 'css-tree/generator'
import parse from 'css-tree/parser'
import walk from 'css-tree/walker'
import { THEMES, importTheme, validToken } from './themes.js'

export const MAX_SKIN_BYTES = 512000
export const MAX_SKINS = 12
export const MAX_LIBRARY_BYTES = 1500000
const bytes = text => new TextEncoder().encode(text).length
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value)
const ID = /^[a-z][a-z0-9-]{0,63}$/
const ASSET = /^data:(image\/(?:png|jpeg|webp|gif)|font\/woff2);base64,([A-Za-z0-9+/]+={0,2})$/
const fail = message => { throw new Error(`${message} Nothing was changed.`) }
const functions = new Set(('var env calc min max clamp round mod rem abs sign pow sqrt hypot log exp sin cos tan asin acos atan atan2 ' +
  'rgb rgba hsl hsla hwb lab lch oklab oklch color color-mix light-dark ' +
  'linear-gradient radial-gradient conic-gradient repeating-linear-gradient repeating-radial-gradient repeating-conic-gradient ' +
  'translate translatex translatey translatez translate3d scale scalex scaley scalez scale3d rotate rotatex rotatey rotatez rotate3d skew skewx skewy matrix matrix3d perspective ' +
  'blur brightness contrast drop-shadow grayscale hue-rotate invert opacity saturate sepia ' +
  'cubic-bezier steps linear repeat minmax fit-content polygon circle ellipse inset rect xywh format local').split(' '))
const atRules = new Set(['media', 'supports', 'container', 'font-face', 'keyframes'])

/** Fail closed on parser recovery, unknown CSS functions and all non-bundled URLs.
 * CSS is presentation code, not a sandbox against misleading/hidden UI. Recovery
 * lives outside the stylesheet (keyboard + URL), and imports never execute JS.
 */
function stylesheet (css, assets) {
  if (typeof css !== 'string' || bytes(css) > 64000) fail('Skin CSS must be text smaller than 64 KB.')
  // Reject escapes rather than allowing escaped URL/function/at-rule names to
  // bypass the allowlist. A style end tag could break out of the preview's style
  // element, even inside a CSS string. Other < and > characters are valid CSS
  // (child combinators and range queries), not HTML injection on their own.
  if (/[\\\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(css) || /<\/style/i.test(css)) fail('CSS escapes, style end tags and control characters are not supported.')
  let ast
  try { ast = parse(css, { parseCustomProperty: true, onParseError: error => { throw error } }) }
  catch { fail('Invalid skin CSS.') }
  let count = 0
  let expandedBytes = bytes(css)
  const assetSizes = new Map()
  walk(ast, function (node) {
    if (++count > 16000) fail('Skin CSS is too complex.')
    if (node.type === 'Raw') fail('Unsupported or malformed skin CSS.')
    if (node.type === 'Atrule' && (!atRules.has(node.name.toLowerCase()) || !node.block)) fail(`Unsupported CSS rule: @${node.name}.`)
    if (node.type === 'Rule' && this.rule) fail('Use flat CSS selectors instead of nested rules.')
    if (node.type === 'Declaration' && /^(?:behavior|-moz-binding)$/i.test(node.property)) fail('Executable CSS is not allowed.')
    if (node.type === 'Function' && !functions.has(node.name.toLowerCase())) fail(`Unsupported CSS function: ${node.name}.`)
    if (node.type === 'Url') {
      const name = node.value.startsWith('skin:') ? node.value.slice(5) : ''
      if (!Object.hasOwn(assets, name)) fail('CSS URLs must reference a bundled asset using url("skin:asset-name").')
      if (!assetSizes.has(name)) assetSizes.set(name, bytes(assets[name]))
      expandedBytes += assetSizes.get(name)
      if (expandedBytes > 2000000) fail('Skin CSS with expanded assets must be smaller than 2 MB.')
    }
  })
  return ast
}

export function validateSkin (data) {
  if (!record(data) || data.format !== 'nautionette-skin' || data.version !== 2) fail('Use a Nautionette skin pack (version 2) or a legacy theme export.')
  if (bytes(JSON.stringify(data)) > MAX_SKIN_BYTES) fail('Skin packs must be smaller than 512 KB.')
  if (typeof data.id !== 'string' || !ID.test(data.id)) fail('Skin id must start with a lowercase letter and contain only letters, digits and hyphens (up to 64 characters).')
  if (typeof data.name !== 'string' || !data.name.trim() || data.name.length > 80) fail('Skin name is required (up to 80 characters).')
  for (const key of ['author', 'description']) {
    if (data[key] !== undefined && (typeof data[key] !== 'string' || data[key].length > 300)) fail(`Invalid skin ${key}.`)
  }
  if (!THEMES.some(theme => theme.id === data.base)) fail('Choose orbit, nebula, daylight or sand as the skin base.')
  if (!record(data.tokens)) fail('Skin tokens must be an object.')
  for (const [key, value] of Object.entries(data.tokens)) {
    if (!validToken(key, value)) fail(`Invalid skin token: ${key}.`)
  }
  const assets = data.assets ?? {}
  if (!record(assets) || Object.keys(assets).length > 24) fail('Use an object with at most 24 bundled assets.')
  for (const [key, value] of Object.entries(assets)) {
    const match = typeof value === 'string' && value.match(ASSET)
    if (!ID.test(key) || !match || match[2].length % 4 !== 0) fail('Assets must be base64 PNG, JPEG, WebP, GIF or WOFF2 data URLs. SVG and remote assets are not supported.')
  }
  stylesheet(data.css, assets)
  // Whitelist export fields: never copy workspace/account data or prototypes.
  return { format: 'nautionette-skin', version: 2, id: data.id, name: data.name.trim(),
    author: data.author || '', description: data.description || '', base: data.base,
    tokens: { ...data.tokens }, css: data.css, assets: { ...assets } }
}

export function importDesign (text) {
  if (typeof text !== 'string' || bytes(text) > MAX_SKIN_BYTES) fail('Skin packs must be JSON smaller than 512 KB.')
  let data
  try { data = JSON.parse(text) } catch { fail('This is not valid JSON.') }
  return data?.version === 1 ? { kind: 'theme', ...importTheme(text) } : { kind: 'skin', skin: validateSkin(data) }
}

export function exportSkin (skin, overrides = {}) {
  return JSON.stringify(validateSkin({ ...skin, tokens: { ...skin.tokens, ...overrides } }), null, 2)
}

export function installSkin (library, input) {
  const skin = validateSkin(input)
  // Reimport replaces the same id, without disturbing unrelated packs.
  const next = [...library.filter(item => item.id !== skin.id), skin]
  if (next.length > MAX_SKINS || bytes(JSON.stringify(next)) > MAX_LIBRARY_BYTES) fail('Skin library is full. Remove a pack before importing another.')
  return next
}

export function sanitizeSkins (input) {
  let result = []
  if (!Array.isArray(input)) return result
  for (const skin of input.slice(0, MAX_SKINS)) {
    try { result = installSkin(result, skin) } catch { /* Bad stored packs must not prevent startup. */ }
  }
  return result
}

/** Each selector is scoped to the active skin. Styles are removed on switch.
 * Font faces/keyframes are global CSS constructs; authors should prefix names
 * with their pack id. Only the active pack's stylesheet is ever attached.
 */
export function compileSkinCss (skin, reduceMotion = false) {
  if (!ID.test(skin.id)) fail('Invalid skin id.')
  const ast = stylesheet(skin.css, skin.assets)
  const scope = `html[data-skin="${skin.id}"]`
  walk(ast, {
    visit: 'Rule',
    enter (node) {
      if (this.atrule?.name.toLowerCase() === 'keyframes') return
      const selectors = node.prelude.children.toArray().map(selector => {
        const text = generate(selector)
        return /^(?:html|:root)(?=[\s.#[:>+~]|$)/.test(text)
          ? text.replace(/^(html|:root)/, scope) : `${scope} ${text}`
      })
      node.prelude = parse(selectors.join(','), { context: 'selectorList' })
    }
  })
  walk(ast, function (node, item, list) {
    if (node.type === 'Url') node.value = skin.assets[node.value.slice(5)]
    if (reduceMotion && node.type === 'Declaration' && /^(?:-(?:webkit|moz)-)?(?:animation|transition|scroll-behavior)(?:-|$)/i.test(node.property)) list.remove(item)
  })
  return generate(ast)
}
