import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(new URL('../../../services/frontend/package.json', import.meta.url))
const { defineConfig } = require('@playwright/test')
const frontend = fileURLToPath(new URL('../../../services/frontend/', import.meta.url))

export default defineConfig({
  testDir: '.',
  testMatch: 'latency.spec.mjs',
  fullyParallel: false,
  workers: 1,
  outputDir: '/workspace/dictation-spike-results',
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:9012' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 9012 --strictPort',
    cwd: frontend,
    url: 'http://127.0.0.1:9012',
    reuseExistingServer: false
  }
})
