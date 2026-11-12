import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { quasar, transformAssetUrls } from '@quasar/vite-plugin'
import { clientBuildInfo } from './scripts/client-build-info.js'

// One codebase, two targets: this builds the web bundle, and Capacitor wraps
// the same dist/ as the app.
export default defineConfig(({ command }) => ({
  define: { __CLIENT_BUILD_INFO__: JSON.stringify(clientBuildInfo(command)) },
  plugins: [
    vue({ template: { transformAssetUrls } }),
    quasar()
  ],
  build: {
    outDir: 'dist',
    target: 'es2022'
  },
  server: {
    port: 9000,
    proxy: {
      '/api': 'http://localhost:8080'
    }
  }
}))
