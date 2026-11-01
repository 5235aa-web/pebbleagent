<template>
  <button
    v-show="visible"
    class="jump-latest tip tip-left"
    data-tip="Jump to latest"
    aria-label="Jump to latest"
    @click="toBottom"
  >
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
  </button>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

/* Anything closer than this counts as already being at the bottom. The value
   matters: a tighter window makes the button flicker while a streamed row is
   still growing taller. */
const NEAR = 80
/* A smooth jump animates for a few hundred milliseconds. Until it lands, the
   scroll events it emits would read as the reader leaving the bottom, so the
   flag is held for this long. */
const SMOOTH_MS = 320

const visible = ref(false)
/** Whether the transcript follows its own growth. Cleared the moment the reader
    scrolls away, restored when they come back within NEAR. */
let stick = true
/** Set while this component animates the offset itself. */
let programmatic = false
/** Last offset either observed or written. Growth leaves this alone; a reader
    scrolling up lowers it. That difference is the only reliable way to tell the
    two apart, because a wheel gesture and its scroll event are not synchronous. */
let lastTop = 0
let raf = 0
let smoothTimer = 0
let observer: ResizeObserver | null = null

function bodyEl(): HTMLElement | null {
  return document.getElementById('streamBody')
}

function gapOf(el: HTMLElement): number {
  return el.scrollHeight - el.scrollTop - el.clientHeight
}

function measure(): void {
  const el = bodyEl()
  if (!el) return
  const gap = gapOf(el)
  if (!programmatic) {
    stick = gap <= NEAR
    lastTop = el.scrollTop
  }
  visible.value = gap > NEAR
}

/** Pins the transcript to its last row. Instant, because a smooth follow never
    catches up with a stream that grows every 120ms. */
function follow(): void {
  const el = bodyEl()
  if (!el || !stick) return
  const target = el.scrollHeight - el.clientHeight
  if (target - el.scrollTop < 1) return
  /* The reader may have scrolled up between the growth and this frame. An offset
     that fell below the last one we know about is theirs, so following stops
     here rather than dragging them back. */
  if (el.scrollTop < lastTop - 2) {
    stick = false
    return
  }
  el.scrollTo({ top: target, behavior: 'instant' })
  lastTop = target
}

/** Growth is the only thing that may move the offset on its own. A scroll event
    never triggers a follow: the reader did that, and pulling the row back is
    exactly the bug this separation exists to prevent. */
function onGrow(): void {
  if (raf) return
  /* Two frames: the first lets layout settle after the height change, the
     second measures the offset the follow actually produced. */
  raf = window.requestAnimationFrame(() => {
    raf = window.requestAnimationFrame(() => {
      raf = 0
      follow()
      measure()
    })
  })
}

/** The reader's own scrolling decides whether following stays on. */
function onScroll(): void {
  if (programmatic) return
  measure()
}

function toBottom(): void {
  const el = bodyEl()
  if (!el) return
  programmatic = true
  window.clearTimeout(smoothTimer)
  smoothTimer = window.setTimeout(() => {
    programmatic = false
    measure()
  }, SMOOTH_MS)
  el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
}

/** A prompt just went out, or another session was opened: those two belong at
    the latest row, wherever the reader had scrolled to. */
function pinToBottom(): void {
  stick = true
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      const el = bodyEl()
      if (!el) return
      el.scrollTo({ top: el.scrollHeight, behavior: 'instant' })
      lastTop = el.scrollTop
      measure()
    })
  })
}

onMounted(() => {
  bodyEl()?.addEventListener('scroll', onScroll, { passive: true })
  /* The reply grows the transcript without firing a scroll event, so the height
     change itself has to drive the follow. */
  const inner = document.getElementById('streamInner')
  if (inner && 'ResizeObserver' in window) {
    observer = new ResizeObserver(onGrow)
    observer.observe(inner)
  }
  document.addEventListener('pebble:stick-bottom', pinToBottom)
  measure()
})

onBeforeUnmount(() => {
  bodyEl()?.removeEventListener('scroll', onScroll)
  observer?.disconnect()
  observer = null
  document.removeEventListener('pebble:stick-bottom', pinToBottom)
  if (raf) window.cancelAnimationFrame(raf)
  if (smoothTimer) window.clearTimeout(smoothTimer)
})
</script>
