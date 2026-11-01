<template>
  <footer class="statusbar">
    <div class="sb-l">
      <span class="sb-item sb-clock" id="clock">{{ clock }}</span>
      <span class="sb-sep"></span>

      <span class="sb-item tip" data-tip="Tool calls">
        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
        {{ toolCalls }}
      </span>
      <span class="sb-item tip sb-hide-sm" data-tip="Skills">
        <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        {{ skillCount }}
      </span>
      <span class="sb-sep"></span>
      <span class="sb-item sb-live" :class="{ off: !online }"><span class="dot"></span>{{ online ? 'connected' : 'offline' }}</span>
      <span class="sb-item sb-hide-sm" style="opacity:.7">session {{ sessionTag }}</span>
    </div>

  </footer>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { sessionState } from '../data/store'
import { streamState } from '../data/stream'

const clock = ref('--:--:--')
let ticker = 0

function tick(): void {
  const d = new Date()
  clock.value = [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, '0')).join(':')
}

onMounted(() => {
  tick()
  ticker = window.setInterval(tick, 1000)
})
onUnmounted(() => window.clearInterval(ticker))

const online = computed(() => streamState.gatewayOnline)
const toolCalls = computed(() => sessionState.toolCalls)
const skillCount = computed(() => sessionState.skillCount)
/* The prototype prints four characters plus an ellipsis; the gateway ids are
   uuids, so the same slice reads the same way. */
const sessionTag = computed(() => {
  const id = streamState.sessionId || sessionState.activeId || ''
  return id ? id.slice(0, 4) + '…' : '----…'
})
</script>
