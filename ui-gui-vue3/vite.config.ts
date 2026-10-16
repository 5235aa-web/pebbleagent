import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

// Dev-only: inject the gateway auth token, read fresh on every page load so a
// backend started after the dev server still works after a refresh. Tauri
// builds receive the token through the get_gateway_token command instead.
function gatewayTokenPlugin() {
  const tokenPath = path.join(os.homedir(), '.pebble', 'gateway.token')
  return {
    name: 'gateway-token-inject',
    transformIndexHtml(html: string) {
      let token = ''
      try { token = fs.readFileSync(tokenPath, 'utf-8').trim() } catch { /* backend not started yet */ }
      if (!token) console.warn(`[gateway-token] ${tokenPath} not found — start the backend, then refresh the page`)
      const snippet = `<script>window.__PEBBLE_GATEWAY_TOKEN__=${JSON.stringify(token)}</script>`
      return html.replace('<head>', `<head>${snippet}`)
    },
  }
}

export default defineConfig({
  plugins: [vue(), gatewayTokenPlugin()],
  server: { port: 5174 },
  clearScreen: false,
  build: {
    minify: 'terser',
    terserOptions: {
      compress: { drop_console: true, drop_debugger: true },
    },
  },
})
