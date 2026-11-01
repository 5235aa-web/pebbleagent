<template>
    <div class="island-wrap">
      <div class="island" :class="{ busy }" id="island">
        <span class="island-pulse"></span>
        <span class="island-text" v-if="busy && toolLabel">Running <b>{{ toolLabel }}</b><template v-if="stepTotal > 0"> · step {{ stepIndex }} of {{ stepTotal }}</template></span>
        <span class="island-text" v-else-if="busy">{{ idleBusyLabel }}</span>
        <span class="island-text" v-else-if="hasRun">Idle · last reply <b>{{ prettyElapsed }}</b></span>
        <span class="island-text" v-else>Idle · waiting for a prompt</span>
        <span class="eq"><i></i><i></i><i></i><i></i><i></i></span>
        <span class="island-meta">
          <span>{{ rateLabel }}</span>
          <span class="sep"></span>
          <span class="im-ctx">{{ usage.contextLimit ? ctxPct + '%' : '—' }}</span>
          <span class="sep"></span>
          <span class="im-cost">{{ costLabel }}</span>
          <span class="sep"></span>
          <span id="islandTimer">{{ elapsedClock }}</span>
        </span>

        <div class="island-expand">
          <div class="ie-title"><span>Live Run</span><span>{{ sessionTitle }}</span></div>
          <div class="ie-row"><span class="k">agent</span><span class="v">{{ agentName }}</span></div>
          <div class="ie-row"><span class="k">model</span><span class="v">{{ modelName }}</span></div>
          <div class="ie-row"><span class="k">tool</span><span class="v">{{ toolDetail }}</span></div>
          <div class="ie-row"><span class="k">context</span><span class="v">{{ contextLabel }}</span></div>
          <div class="ie-row"><span class="k">cost</span><span class="v ie-cost">{{ costDetail }}</span></div>
          <div class="ie-row"><span class="k">elapsed</span><span class="v">{{ prettyElapsed }}</span></div>
          <div class="ie-bar"><i :style="{ width: stepPct + '%' }"></i></div>
        </div>
      </div>
    </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { streamState } from '../data/stream'
import { usage, context, llm } from '../data/panels'
import { plan, activeSession } from '../data/store'
import { runTiming } from '../data/wire'

/* The clock ticks on its own so the readout moves while the agent works, but it
   stops being authoritative the moment the run ends: `runTiming` then holds the
   final figure and the next prompt clears it back to zero. */
const tick = ref(Date.now())
let timer = 0

onMounted(() => {
  timer = window.setInterval(() => { tick.value = Date.now() }, 500)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})

const elapsedMs = computed(() =>
  runTiming.running ? Math.max(0, tick.value - runTiming.startAt) : runTiming.elapsedMs
)
const hasRun = computed(() => runTiming.running || runTiming.elapsedMs > 0)

function two(n: number): string {
  return String(n).padStart(2, '0')
}

function clockOf(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  return two(Math.floor(s / 60)) + ':' + two(s % 60)
}

function prettyOf(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  return s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + two(s % 60) + 's'
}

function num(n: number): string {
  return n.toLocaleString('en-US')
}

const busy = computed(() => streamState.streaming)

/** The row the agent is writing into right now. Scanning back for the streaming
    row rather than taking the last one keeps this correct while a clarify card
    or a freshly sent user row sits after it. */
const liveRow = computed(() => {
  const items = streamState.items
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].streaming) return items[i]
  }
  return null
})

/* Its blocks carry every tool card in the order they ran, so the last one is the
   call that either runs now or just finished — the only live signal there is
   when the agent keeps no todo list. */
const liveTool = computed(() => {
  const row = liveRow.value
  if (!row || !row.blocks) return null
  for (let i = row.blocks.length - 1; i >= 0; i--) {
    const b = row.blocks[i]
    if (b.kind === 'tool') return b.call
  }
  return null
})

function isRunning(status: string): boolean {
  return status === 'running' || status === 'streaming'
}

/* The kind of the last block the row produced. Reasoning and the answer itself
   arrive through the same stream but read differently to someone watching, so
   the headline distinguishes them instead of calling both "Thinking". */
const liveBlockKind = computed(() => {
  const row = liveRow.value
  if (!row || !row.blocks || !row.blocks.length) return ''
  return row.blocks[row.blocks.length - 1].kind
})

/** The headline for a run with no tool in flight. */
const idleBusyLabel = computed(() => {
  const k = liveBlockKind.value
  return k === 'raw' || k === 'text' || k === 'code' || k === 'mermaid' || k === 'svg' ? 'Writing…' : 'Thinking…'
})
const elapsedClock = computed(() => clockOf(elapsedMs.value))
const prettyElapsed = computed(() => prettyOf(elapsedMs.value))
const sessionTitle = computed(() => activeSession()?.title?.trim() || 'no session')

const agentName = computed(() => llm.agents.find(a => a.current)?.name || context.agentName || 'agent')
const modelName = computed(() => streamState.model || llm.model || 'model')

const currentStep = computed(() => plan.steps.find(s => s.state === 'now') ?? null)
const stepTotal = computed(() => plan.steps.length)
const stepIndex = computed(() => {
  const i = plan.steps.findIndex(s => s.state === 'now')
  if (i >= 0) return i + 1
  return Math.min(plan.steps.length, plan.steps.filter(s => s.state === 'done').length + 1) || 0
})
const stepPct = computed(() => {
  if (!stepTotal.value) return 0
  const done = plan.steps.filter(s => s.state === 'done').length
  return Math.round((done / stepTotal.value) * 100)
})
/* What the run is doing, in priority order: a tool that is running right now,
   then the current todo step, then the plain fact that tokens are streaming.
   Reading only the todo list is why both the headline and the expanded `tool`
   row said "Thinking…" and "idle" through every turn that had no plan. */
const toolLabel = computed(() => {
  const call = liveTool.value
  if (call && isRunning(call.status)) return call.name
  const note = (currentStep.value?.note ?? '').split('·')[0].trim()
  return note || currentStep.value?.task || ''
})
const toolDetail = computed(() => {
  const call = liveTool.value
  if (call) {
    const arg = (call.arg ?? '').trim()
    const body = call.name + (arg ? ' · ' + arg : '')
    return isRunning(call.status) ? body : body + ' · ' + call.status
  }
  if (currentStep.value) return toolLabel.value + ' · ' + currentStep.value.task
  if (busy.value) return liveBlockKind.value === 'thinking' ? 'thinking' : 'writing'
  return 'idle'
})

const ctxPct = computed(() => usage.contextPct)
const ctxLimit = computed(() => {
  if (usage.contextLimit) return usage.contextLimit
  return usage.contextPct > 0 ? Math.round(usage.contextTokens / (usage.contextPct / 100)) : 0
})
const contextLabel = computed(() => {
  const used = usage.contextTokens
  // No usage payload yet for this run: report the window share rather than a
  // bare zero that reads like a broken counter.
  if (!used) return ctxPct.value + '% of window'
  return ctxLimit.value ? num(used) + ' / ' + num(ctxLimit.value) : num(used) + ' tokens'
})
const costLabel = computed(() => '$' + (usage.costToday ?? 0).toFixed(2))
const costDetail = computed(
  () => '$' + (usage.sessionCost ?? 0).toFixed(3) + ' session · ' + usage.calls + ' calls'
)
/* No constant rates: the figure is characters the live row has actually produced
   divided by the run's wall clock, using the same 3.6 characters-per-token
   estimate the composer shows. Before anything lands it reads as a dash rather
   than a plausible-looking lie. */
const liveChars = computed(() => (liveRow.value?.plain ?? '').length)
const rateLabel = computed(() => {
  const ms = elapsedMs.value
  const chars = liveChars.value
  if (!ms || !chars) return '— tok/s'
  return (chars / 3.6 / (ms / 1000)).toFixed(1) + ' tok/s'
})
</script>
