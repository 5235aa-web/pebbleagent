import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

// The version badge must name the build it was compiled into, so the number is
// read from the one file the release script rewrites and the updater compares
// against. package.json drifts (it still says 0.1.0); tauri.conf.json cannot,
// because `npm run release -- 1.0.3` writes it before the build starts.
const tauriConfPath = fileURLToPath(new URL('./src-tauri/tauri.conf.json', import.meta.url))
const appVersion: string = JSON.parse(readFileSync(tauriConfPath, 'utf8')).version

// whitespace: 'preserve' keeps the hand-written HTML spacing intact.
// The prototype relies on newline-generated gaps between inline elements;
// Vue's default 'condense' would collapse them and shift layout by a few px.
//
// The token plugin is dev-only. A browser cannot read the token file itself, and
// the gateway closes any unauthenticated handshake with 4401, so the dev server
// hands the value to the page. apply: 'serve' keeps it out of a production
// build, where the Tauri shell provides the token through get_gateway_token.
function gatewayToken(): string {
  const home = process.env.PEBBLE_HOME || join(homedir(), '.pebble')
  try {
    return readFileSync(join(home, 'gateway.token'), 'utf8').trim()
  } catch {
    // First launch: the backend has not written the file yet. The page still
    // loads; gateway.ts resolves the token per connection attempt.
    return ''
  }
}

function injectGatewayToken(): Plugin {
  return {
    name: 'pebble-inject-gateway-token',
    apply: 'serve',
    transformIndexHtml(html) {
      const token = gatewayToken()
      if (!token) return html
      const tag = `<script>window.__PEBBLE_GATEWAY_TOKEN__=${JSON.stringify(token)};</script>`
      return html.replace('</head>', `  ${tag}\n</head>`)
    }
  }
}

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(appVersion)
  },
  plugins: [
    vue({
      template: {
        compilerOptions: { whitespace: 'preserve' }
      }
    }),
    injectGatewayToken()
  ],
  server: { port: 5180, strictPort: true },
  build: { target: 'chrome110', cssMinify: false }
})
