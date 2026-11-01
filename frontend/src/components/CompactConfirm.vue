<template>
  <div class="overlay" id="compactConfirm" aria-hidden="true">
    <div class="sheet cm">
      <div class="sheet-hdr">
        <span class="sheet-ico">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <polyline points="4 14 10 14 10 20" />
            <polyline points="20 10 14 10 14 4" />
            <line x1="14" y1="10" x2="21" y2="3" />
            <line x1="3" y1="21" x2="10" y2="14" />
          </svg>
        </span>
        <div class="sheet-title">
          Compact context
          <small>fold the earlier turns into one summary</small>
        </div>
        <button class="sheet-x" data-close aria-label="Close">&#215;</button>
      </div>

      <div class="cm-body">
        <p class="cm-lead">
          Every message above the new boundary is replaced by a single summary card in the transcript.
          Nothing leaves the session, the card expands on click, and the model keeps working from that
          summary instead of the raw turns.
        </p>
        <div class="cm-rows">
          <div class="cm-row"><span>scope</span><b>messages above the boundary</b></div>
          <div class="cm-row"><span>result</span><b>one summary card, collapsible</b></div>
          <div class="cm-row"><span>runs on</span><b>gateway · session.compress</b></div>
        </div>
      </div>

      <div class="sheet-foot">
        <span class="sf-hint">this cannot be undone from the transcript</span>
        <button class="btn btn-ghost btn-sm" data-close :disabled="busy">Cancel</button>
        <button class="btn btn-primary btn-sm" :disabled="busy" @click="run">
          {{ busy ? 'Compacting…' : 'Compact' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { compressSession } from '../data/wire'

/* The gateway owns the summary; this dialog only decides whether to ask it. */
const busy = ref(false)

type ToastFn = (title: string, message: string, kind?: 'ok' | 'warn') => void

function toast(title: string, message: string, kind: 'ok' | 'warn') {
  const fn = (window as unknown as { __pebbleToast?: ToastFn }).__pebbleToast
  if (fn) fn(title, message, kind)
}

function close() {
  const el = document.getElementById('compactConfirm')
  el?.classList.remove('open')
  el?.setAttribute('aria-hidden', 'true')
}

async function run() {
  if (busy.value) return
  busy.value = true
  try {
    const outcome = await compressSession()
    toast(outcome.title, outcome.message, outcome.ok ? 'ok' : 'warn')
    // A failed run leaves the sheet open so the same decision can be retried
    // without reopening the dialog.
    if (outcome.ok) close()
  } finally {
    busy.value = false
  }
}
</script>
