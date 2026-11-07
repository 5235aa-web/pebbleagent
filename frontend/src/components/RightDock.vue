<template>
    <aside class="dock-right">
      <DockHead />

      <div
        class="dr-content"
        :class="{ 'scroll-hot': scrollHot }"
        @mouseenter="showScrollbar"
        @mouseleave="hideScrollbarLater"
      >

        <!-- FILES -->
      <FilesPane />


        <!-- USAGE -->
      <UsagePane />

        <!-- CONTEXT -->
      <ContextPane />

        <!-- LLM -->


      <LlmPane />

        <!-- NOTES -->
      <NotesPane />

        <!-- GITHUB -->
      <GithubPane />

      </div>
    </aside>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import DockHead from './DockHead.vue'
import FilesPane from './FilesPane.vue'
import UsagePane from './UsagePane.vue'
import ContextPane from './ContextPane.vue'
import LlmPane from './LlmPane.vue'
import NotesPane from './NotesPane.vue'
import GithubPane from './GithubPane.vue'

/* The dock's scrollbar is drawn transparent until the pointer is over the
   content (app.css owns that part) and cleared again shortly after it leaves.
   The delay is the whole point: without it a pointer crossing the panel on its
   way somewhere else flashes an 8px rail. */
const SCROLLBAR_HIDE_DELAY = 500
const scrollHot = ref(false)
let hideTimer: number | null = null

function showScrollbar(): void {
  if (hideTimer !== null) {
    window.clearTimeout(hideTimer)
    hideTimer = null
  }
  scrollHot.value = true
}

function hideScrollbarLater(): void {
  if (hideTimer !== null) window.clearTimeout(hideTimer)
  hideTimer = window.setTimeout(() => {
    scrollHot.value = false
    hideTimer = null
  }, SCROLLBAR_HIDE_DELAY)
}

onBeforeUnmount(() => {
  if (hideTimer !== null) window.clearTimeout(hideTimer)
  hideTimer = null
})
</script>
