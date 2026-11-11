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
        <div v-if="failure" class="cm-result" role="alert">
          <span class="cm-result-ico">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <line x1="12" y1="8" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </span>
          <div class="cm-result-main">
            <b>Compaction failed</b>
            <span>{{ failure }}</span>
            <span class="cm-result-hint">Nothing was folded and the transcript is unchanged, so it is safe to run again.</span>
          </div>
        </div>
      </div>

      <div class="sheet-foot">
        <span class="sf-hint">{{ failure ? 'nothing was changed' : 'this cannot be undone from the transcript' }}</span>
        <button class="btn btn-ghost btn-sm" data-close :disabled="busy">Cancel</button>
        <button class="btn btn-primary btn-sm" :disabled="busy" @click="run">
          {{ busy ? 'Compacting…' : (failure ? 'Try again' : 'Compact') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { compressSession } from '../data/wire'

/* The gateway owns the summary; this dialog only decides whether to ask it. */
const busy = ref(false)
/* Why the last run failed. Shown inside the sheet: the toast is a fallback for a
   dialog that was closed, not the place this answer is normally read. */
const failure = ref('')

/* The sheet is opened by toggling a class from the shell, so the component is
   never remounted. Without this, reopening it would show the previous run's
   failure as if it had just happened. */
let observer: MutationObserver | null = null
onMounted(() => {
  const el = document.getElementById('compactConfirm')
  if (!el) return
  observer = new MutationObserver(() => {
    /* Clear on close, not on open: a run that fails while the sheet is already
       closed has to be readable when it is opened again. */
    if (!el.classList.contains('open')) failure.value = ''
  })
  observer.observe(el, { attributes: true, attributeFilter: ['class'] })
})
onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
})

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
  failure.value = ''
  try {
    const outcome = await compressSession()
    if (outcome.ok) {
      toast(outcome.title, outcome.message, 'ok')
      close()
      return
    }
    /* A failed run keeps the sheet open with the reason on screen, so the same
       decision can be retried without reopening the dialog and without hunting
       for a corner toast that has already expired. */
    failure.value = outcome.message || outcome.title
    toast(outcome.title, outcome.message, 'warn')
  } finally {
    busy.value = false
  }
}
</script>
