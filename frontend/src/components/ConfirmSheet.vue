<template>
  <div
    class="overlay"
    id="confirmSheet"
    :class="{ open: confirmState.open }"
    :aria-hidden="confirmState.open ? 'false' : 'true'"
    @click.self="settleConfirm(false)"
  >
    <div class="sheet cm" role="dialog" aria-modal="true" :aria-label="confirmState.title">
      <div class="sheet-hdr">
        <span class="sheet-ico">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M10.3 3.9 2.4 17.1A2 2 0 0 0 4.1 20h15.8a2 2 0 0 0 1.7-2.9L13.7 3.9a2 2 0 0 0-3.4 0Z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12" y2="17.01" />
          </svg>
        </span>
        <div class="sheet-title">
          {{ confirmState.title }}
          <small>{{ confirmState.hint }}</small>
        </div>
        <button class="sheet-x" aria-label="Close" @click="settleConfirm(false)">&#215;</button>
      </div>

      <div class="cm-body">
        <p class="cm-lead">{{ confirmState.body }}</p>
      </div>

      <div class="sheet-foot">
        <span class="sf-hint">{{ confirmState.danger ? 'this cannot be undone' : '' }}</span>
        <button class="btn btn-ghost btn-sm" @click="settleConfirm(false)">{{ confirmState.cancelLabel }}</button>
        <button
          class="btn btn-sm"
          :class="confirmState.danger ? 'btn-danger' : 'btn-primary'"
          @click="settleConfirm(true)"
        >{{ confirmState.confirmLabel }}</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { confirmState, settleConfirm } from '../data/confirm'

/* Escape closes the sheet, matching every other overlay in the shell. Enter is
   deliberately not bound to confirm: the destructive variant must take a real
   click, not a keystroke that was meant for whatever opened it. */
function onKey(e: KeyboardEvent): void {
  if (!confirmState.open) return
  if (e.key === 'Escape') settleConfirm(false)
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>
