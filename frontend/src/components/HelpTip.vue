<template>
  <span class="help-tip" ref="rootEl">
    <button
      ref="btnEl"
      type="button"
      class="help-tip-btn"
      :class="{ on: open }"
      :aria-expanded="open ? 'true' : 'false'"
      :aria-label="label || title"
      @click.stop="toggle"
    >?</button>

    <div
      v-if="open"
      ref="popEl"
      class="help-tip-pop"
      role="dialog"
      :aria-label="title"
      :style="popStyle"
      @click.stop
    >
      <header class="help-tip-hdr">
        <span class="help-tip-title">{{ title }}</span>
        <button type="button" class="help-tip-x" aria-label="Close help" @click="close">&#215;</button>
      </header>
      <div class="help-tip-body">
        <slot />
      </div>
    </div>
  </span>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  label?: string
  width?: number
}>(), {
  label: '',
  width: 312
})

const open = ref(false)
const rootEl = ref<HTMLElement | null>(null)
const btnEl = ref<HTMLElement | null>(null)
const popEl = ref<HTMLElement | null>(null)
const leftOffset = ref(0)
const arrowOffset = ref(12)

const popStyle = ref<Record<string, string>>({})

function updatePosition(): void {
  const btn = btnEl.value
  if (!btn) return
  const host = btn.closest('.dr-content') as HTMLElement | null
  const hostRect = host
    ? host.getBoundingClientRect()
    : ({ left: 8, right: window.innerWidth - 8, width: window.innerWidth - 16 } as DOMRect)
  const btnRect = btn.getBoundingClientRect()
  const pad = 12
  const maxWidth = Math.max(220, Math.min(props.width, hostRect.width - pad * 2))
  /* Center roughly over the trigger, then clamp inside the dock content box so
     the popover never clips at either edge of the 384px panel. */
  const desiredLeft = btnRect.left + btnRect.width / 2 - maxWidth / 2
  const minLeft = hostRect.left + pad
  const maxLeft = hostRect.right - pad - maxWidth
  const clampedLeft = Math.max(minLeft, Math.min(desiredLeft, maxLeft))
  leftOffset.value = Math.round(clampedLeft - btnRect.left)
  arrowOffset.value = Math.round(
    Math.max(12, Math.min(maxWidth - 12, btnRect.left + btnRect.width / 2 - clampedLeft))
  )
  popStyle.value = {
    width: `${maxWidth}px`,
    left: `${leftOffset.value}px`,
    '--tip-arrow-x': `${arrowOffset.value}px`
  }
}

function toggle(): void {
  open.value = !open.value
  if (open.value) {
    void nextTick(updatePosition)
  }
}

function close(): void {
  open.value = false
}

function onPointerDown(e: Event): void {
  if (!open.value) return
  if (rootEl.value && rootEl.value.contains(e.target as Node)) return
  close()
}

function onKeyDown(e: KeyboardEvent): void {
  if (open.value && e.key === 'Escape') {
    e.stopPropagation()
    close()
  }
}

onMounted(() => {
  document.addEventListener('pointerdown', onPointerDown, true)
  document.addEventListener('keydown', onKeyDown, true)
  window.addEventListener('resize', updatePosition)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onPointerDown, true)
  document.removeEventListener('keydown', onKeyDown, true)
  window.removeEventListener('resize', updatePosition)
})
</script>
