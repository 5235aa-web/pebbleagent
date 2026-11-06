<template>
      <div class="dr-head">
        <div class="dr-tabwrap" ref="wrapEl">
          <div class="dr-tabs" ref="tabsEl">
            <button class="dr-tab on" data-tab="files">Files <span class="cnt">7</span></button>
            <button class="dr-tab" data-tab="usage">Usage</button>
            <button class="dr-tab" data-tab="context">Context</button>
            <button class="dr-tab" data-tab="llm">LLM</button>
            <button class="dr-tab" data-tab="notes">Notes</button>
            <!-- Icon-only: a sixth word would push the strip past its width on
                 a 1280px window, and the mark is unambiguous on its own. It has
                 no tooltip because `.dr-tabs` clips overflow, which would cut
                 the bubble off. -->
            <button class="dr-tab dr-tab-ico" data-tab="github" aria-label="GitHub repositories">
              <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
            </button>
          </div>
          <button class="dr-nav prev" type="button" aria-label="Scroll tabs left" @click="nudge(-1)">
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
          </button>
          <button class="dr-nav next" type="button" aria-label="Scroll tabs right" @click="nudge(1)">
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
          </button>
        </div>
        <button class="icon-btn tip tip-right" data-tip="Open preview stage" id="openStage">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2.5" y="4" width="19" height="13" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
        </button>
        <button class="icon-btn tip tip-right" data-tip="Collapse dock" id="collapseRight">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="9 18 15 12 9 6"/></svg>
        </button>
      </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

const wrapEl = ref<HTMLElement | null>(null)
const tabsEl = ref<HTMLElement | null>(null)

/* One fade width, shared with the mask in app.css. The reveal padding and the
   arrow geometry both derive from it, so the three never drift apart. */
const FADE = 18

let resizeObserver: ResizeObserver | null = null
let classObserver: MutationObserver | null = null

function parts(): { wrap: HTMLElement; el: HTMLElement } | null {
  const wrap = wrapEl.value
  const el = tabsEl.value
  return wrap && el ? { wrap, el } : null
}

/* One place decides three things at once: whether the strip overflows, which
   edge still hides a tab, and whether the arrows are reachable. Scroll, resize
   and every class mutation can change all three, so all three call this. */
function sync(): void {
  const p = parts()
  if (!p) return
  const { wrap, el } = p
  const max = el.scrollWidth - el.clientWidth
  const overflows = max > 1
  const atStart = el.scrollLeft <= 1
  const atEnd = el.scrollLeft >= max - 1
  wrap.classList.toggle('scrollable', overflows)
  el.classList.toggle('fade-l', overflows && !atStart)
  el.classList.toggle('fade-r', overflows && !atEnd)
  const prev = wrap.querySelector<HTMLButtonElement>('.dr-nav.prev')
  const next = wrap.querySelector<HTMLButtonElement>('.dr-nav.next')
  if (prev) prev.disabled = !overflows || atStart
  if (next) next.disabled = !overflows || atEnd
}

/* `selectTab` in orbit/runtime.js only toggles classes, so a tab reached from
   the command palette can land outside the visible strip with nothing to show
   it was selected. The class attribute is the only hook: that path never
   dispatches a click, and the runtime sheet is frozen. */
function reveal(tab: HTMLElement): void {
  const el = tabsEl.value
  if (!el) return
  const tr = tab.getBoundingClientRect()
  const cr = el.getBoundingClientRect()
  /* The mask fades the last 18px of the strip, so a tab that is geometrically
     visible can still sit in the fade and read as half-erased. The padding is
     one fade width plus a hair, and it has to stay in step with app.css. */
  const pad = FADE + 2
  if (tr.left < cr.left) el.scrollLeft -= cr.left - tr.left + pad
  else if (tr.right > cr.right) el.scrollLeft += tr.right - cr.right + pad
}

function onWheel(e: WheelEvent): void {
  const el = tabsEl.value
  if (!el) return
  /* A trackpad's horizontal gesture already works, and a wheel with nowhere to
     go must keep scrolling the page, so both leave the event alone. */
  if (Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return
  if (el.scrollWidth - el.clientWidth <= 1) return
  e.preventDefault()
  /* deltaMode 1 is lines; Chromium never sends it, but the guard is free. */
  el.scrollLeft += e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
}

function nudge(dir: number): void {
  const el = tabsEl.value
  if (!el) return
  const step = Math.max(72, Math.round(el.clientWidth * 0.6))
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  el.scrollBy({ left: dir * step, behavior: reduce ? 'auto' : 'smooth' })
}

onMounted(() => {
  const p = parts()
  if (!p) return
  const { el } = p
  /* `passive: false` is the point of binding by hand: the handler calls
     preventDefault, which a passive listener cannot do. */
  el.addEventListener('wheel', onWheel, { passive: false })
  el.addEventListener('scroll', sync, { passive: true })
  resizeObserver = new ResizeObserver(sync)
  resizeObserver.observe(el)
  classObserver = new MutationObserver(records => {
    for (const r of records) {
      const t = r.target as HTMLElement
      if (t.classList?.contains('on')) {
        reveal(t)
        break
      }
    }
    sync()
  })
  classObserver.observe(el, { attributes: true, attributeFilter: ['class'], subtree: true })
  sync()
})

onBeforeUnmount(() => {
  const el = tabsEl.value
  if (el) {
    el.removeEventListener('wheel', onWheel)
    el.removeEventListener('scroll', sync)
  }
  resizeObserver?.disconnect()
  classObserver?.disconnect()
  resizeObserver = null
  classObserver = null
})
</script>
