// The generated desktop manifest is also bundled in every native build context.
import { readFileSync } from 'node:fs'

export const brand = JSON.parse(readFileSync(new URL('../desktop/brand.json', import.meta.url), 'utf8'))
export const xmlText = value => value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char])

export function configureAndroidStrings (strings) {
  for (const key of ['app_name', 'title_activity_main']) {
    const entry = `<string name="${key}">${xmlText(brand.name)}</string>`
    const pattern = new RegExp(`<string name="${key}">[\\s\\S]*?</string>`)
    strings = pattern.test(strings) ? strings.replace(pattern, () => entry) : strings.replace('</resources>', `    ${entry}\n</resources>`)
  }
  return strings
}
