<template>
  <button
    v-if="supported"
    class="icon-btn tip up-btn"
    :class="{ 'is-live': live, 'is-busy': busy }"
    :data-tip="tip"
    :disabled="busy"
    @click="run"
  >
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
      <path d="M12 3v11" />
      <polyline points="7.5 9.5 12 14 16.5 9.5" />
      <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
    </svg>
    <span v-if="live" class="up-dot" />
  </button>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

type ToastFn = (title: string, message: string, kind?: string) => void

/** The toast stack belongs to runtime.js and is shared through the window, so
    the updater reports through that host instead of growing a second one. */
function toast(title: string, message: string, kind: 'ok' | 'warn' | 'err' = 'ok'): void {
  const fn = (window as unknown as { __pebbleToast?: ToastFn }).__pebbleToast
  if (fn) fn(title, message, kind)
}

/* `check()` returns an Update when the endpoint advertises a newer semver, and
   null when this build is current — so the button has three resting states:
   quiet, "update available", and "restart to finish". The download phase is the
   only one long enough to need progress, and it reports the chunk count the
   plugin hands back. */
type Phase = 'idle' | 'checking' | 'available' | 'downloading' | 'ready' | 'current' | 'error'

const supported = ref(false)
const phase = ref<Phase>('idle')
const version = ref('')
const percent = ref(0)

let pending: { version: string; downloadAndInstall: (cb: (e: unknown) => void) => Promise<void> } | null = null
let timer = 0

const busy = computed(() => phase.value === 'checking' || phase.value === 'downloading')
const live = computed(() => phase.value === 'available' || phase.value === 'ready')

const tip = computed(() => {
  switch (phase.value) {
    case 'checking': return 'Checking for updates…'
    case 'available': return `Update ${version.value} available — click to install`
    case 'downloading': return `Downloading ${percent.value}%`
    case 'ready': return 'Restart to finish updating'
    case 'current': return 'Up to date'
    case 'error': return 'Update check failed — click to retry'
    default: return 'Check for updates'
  }
})

function rest(): void {
  window.clearTimeout(timer)
  timer = window.setTimeout(() => {
    if (phase.value === 'current' || phase.value === 'error') phase.value = 'idle'
  }, 3200)
}

async function checkNow(silent = false): Promise<void> {
  if (!silent) phase.value = 'checking'
  try {
    const { check } = await import('@tauri-apps/plugin-updater')
    const found = await check()
    if (found) {
      pending = found as unknown as typeof pending
      version.value = found.version
      phase.value = 'available'
      return
    }
    if (!silent) {
      phase.value = 'current'
      rest()
      /* A manual click has to answer: the button returns to its resting shape in
         a few seconds, which reads as "nothing happened" without this. */
      toast('No update', "You're on the latest version.")
    }
  } catch (err) {
    console.warn('[Updater] check failed', err)
    if (!silent) {
      phase.value = 'error'
      rest()
      toast('Update check failed', 'Could not reach the release feed.', 'warn')
    }
  }
}

async function install(): Promise<void> {
  if (!pending) return
  phase.value = 'downloading'
  percent.value = 0
  let total = 0
  let received = 0
  try {
    await pending.downloadAndInstall((event: unknown) => {
      const e = (event ?? {}) as { event?: string; data?: { contentLength?: number; chunkLength?: number } }
      if (e.event === 'Started') total = Number(e.data?.contentLength ?? 0)
      else if (e.event === 'Progress') {
        received += Number(e.data?.chunkLength ?? 0)
        if (total > 0) percent.value = Math.min(99, Math.round((received / total) * 100))
      }
    })
    percent.value = 100
    phase.value = 'ready'
  } catch (err) {
    console.warn('[Updater] install failed', err)
    phase.value = 'error'
    rest()
  }
}

async function restart(): Promise<void> {
  try {
    const { relaunch } = await import('@tauri-apps/plugin-process')
    await relaunch()
  } catch (err) {
    console.warn('[Updater] relaunch failed', err)
    phase.value = 'error'
    rest()
  }
}

function run(): void {
  if (phase.value === 'ready') { void restart(); return }
  if (phase.value === 'available') { void install(); return }
  if (busy.value) return
  void checkNow()
}

onMounted(() => {
  /* Browsers have no updater; the button only exists inside the packaged shell.
     The first look is silent so a launch does not flash a spinner. */
  supported.value = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
  if (supported.value) void checkNow(true)
})
</script>
