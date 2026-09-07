import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:9012', viewport: { width: 1440, height: 1000 } },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 9012 --strictPort',
    url: 'http://127.0.0.1:9012',
    reuseExistingServer: !process.env.CI
  }
})