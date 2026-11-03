/// <reference types="vite/client" />

/** Injected by vite.config.ts from src-tauri/tauri.conf.json at build time. */
declare const __APP_VERSION__: string

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}
