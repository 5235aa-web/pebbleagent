<template>
  <div :class="rowClass" data-live="1" @click="onClick">
    <span class="tr-node">
      <span class="tr-glyph" v-html="icon"></span>
      <span class="tr-ring" v-if="running"></span>
    </span>
    <span class="tr-rail">
      <span class="tr-spark" v-if="running"></span>
      <span class="tr-tick" v-if="settled"></span>
    </span>
    <div class="tr-head">
      <span class="tr-name">{{ call.name }}</span>
      <span class="tr-arg" v-if="call.arg">{{ call.arg }}</span>
      <span class="tr-dur">{{ dur }}</span>
      <span class="tr-state">{{ stateWord }}</span>
    </div>
    <div class="tr-body" v-if="inner"><div><div class="tr-out" v-html="inner"></div></div></div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { toolIcon, escapeHtml, type ToolCallView } from '../data/render'

const props = defineProps<{ call: ToolCallView }>()

// A transcript can hold thousands of calls; expanding them all turns the page
// into a wall of output. Collapsed by default, the same way the production
// shell's ToolExecutionCard behaves.
const open = ref(false)

/* Opening is only meaningful when there is something underneath: a read that
   summarises itself as "already read" still has a payload, a call with no
   recorded output does not. */
function onClick() {
  if (!inner.value) return
  open.value = !open.value
}

const running = computed(() => props.call.status === 'running' || props.call.status === 'streaming')
const settled = computed(() => !running.value)
const rowClass = computed(() => {
  const tone = props.call.status === 'completed' ? 'is-done'
    : (props.call.status === 'failed' || props.call.status === 'cancelled') ? 'is-failed'
      : 'is-running'
  return 'tr ' + tone + (open.value ? ' open' : '')
})
const icon = computed(() => toolIcon(props.call.name))
const dur = computed(() => {
  const d = props.call.durationS
  return d ? d.toFixed(d < 10 ? 2 : 1) + 's' : ''
})
/* The word is the second carrier next to colour: a reader who cannot separate
   the accent from the success green still gets the state. */
const stateWord = computed(() => {
  const s = props.call.status
  return s === 'completed' ? 'done' : s === 'failed' ? 'failed' : s === 'cancelled' ? 'cancelled' : 'running'
})
/* A read is a fact, not a payload. The file still travels to the model in
   `call.output` and in the gateway record; only the card stops printing it,
   because nobody wants to scroll a thousand lines of source they just asked the
   agent to look at. */
const READ_TOOLS = /^(read_file|read_text|read_many|read_multiple_files|read|cat|view|open_file|list_dir)$/i

const inner = computed(() => {
  const c = props.call
  if (c.error) return term(c.error, true)
  if (READ_TOOLS.test(c.name) && c.status === 'completed') {
    return '<div class="tool-note"><span class="tn-dot"></span>already read</div>'
  }
  if (c.output) return term(c.output, false)
  if (c.input) return '<div class="tool-kv"><span class="k">input</span><span class="v">' + escapeHtml(c.input) + '</span></div>'
  if (c.status === 'streaming' && c.arg) return '<div class="tool-kv"><span class="k">streaming</span><span class="v">' + escapeHtml(c.arg) + '</span></div>'
  // Completed and failed calls that recorded no output have nothing to show;
  // say so instead of opening onto an empty panel.
  if (c.status === 'completed' || c.status === 'failed') return '<div class="tool-note muted">output not recorded</div>'
  return ''
})

function term(text: string, isError: boolean): string {
  const body = text.split('\n')
    .map(line => (isError ? '<span class="t-err">' + escapeHtml(line) + '</span>' : escapeHtml(line)))
    .join('\n')
  return '<div class="term">' + body + '</div>'
}
</script>
