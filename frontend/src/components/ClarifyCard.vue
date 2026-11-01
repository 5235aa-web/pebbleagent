<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ClarifyQuestion } from '../data/panels'

const props = defineProps<{ requestId: string; questions: ClarifyQuestion[] }>()
const emit = defineEmits<{
  (e: 'submit', answers: Record<string, string>): void
  (e: 'skip'): void
}>()

const step = ref(0)
const picked = ref<string[][]>(props.questions.map(() => []))
const done = ref(false)
const stampText = ref('Answered')

const current = computed<ClarifyQuestion | undefined>(() => props.questions[step.value])
const total = computed(() => props.questions.length)
const stepNo = computed(() => String(step.value + 1).padStart(2, '0'))
const pickedHere = computed(() => picked.value[step.value] ?? [])
const isPicked = (label: string) => pickedHere.value.includes(label)

function choose(label: string) {
  if (done.value || !current.value) return
  const row = picked.value[step.value]
  if (!row) return
  if (current.value.multiple) {
    const at = row.indexOf(label)
    if (at > -1) row.splice(at, 1)
    else row.push(label)
  } else {
    row.splice(0, row.length, label)
  }
}

function collect(): Record<string, string> {
  const out: Record<string, string> = {}
  props.questions.forEach((q, i) => {
    const sel = picked.value[i] ?? []
    if (sel.length) out[q.prompt] = sel.join(', ')
  })
  return out
}

function advance() {
  if (done.value) return
  if (step.value < total.value - 1) {
    step.value += 1
    return
  }
  done.value = true
  emit('submit', collect())
}

function skip() {
  if (done.value) return
  done.value = true
  stampText.value = 'Skipped'
  emit('skip')
}
</script>

<template>
  <div class="qcard" :class="{ done }" data-live="1">
    <span class="q-stub" aria-hidden="true"><i></i><i></i><i></i></span>
    <div class="q-hdr">
      <span class="q-mark">?</span>
      <span class="q-kicker">{{ (current?.title || 'question').toLowerCase() }}</span>
      <span class="q-title">Needs your decision</span>
      <span v-if="total > 1" class="q-step"><b>{{ step + 1 }}</b><span>/ {{ total }}</span></span>
    </div>
    <div class="q-body">
      <div class="q-ask">
        <span class="q-no">{{ stepNo }}</span>
        <span class="q-ask-text">{{ current?.prompt }}</span>
      </div>
      <div class="q-options">
        <button
          v-for="opt in current?.options ?? []"
          :key="opt.label"
          class="q-opt"
          :class="{ sel: isPicked(opt.label) }"
          type="button"
          @click="choose(opt.label)"
        >
          <span class="q-box"><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12" /></svg></span>
          <span class="q-opt-text">
            <span class="q-opt-label">{{ opt.label }}</span>
            <span v-if="opt.detail" class="q-opt-desc">{{ opt.detail }}</span>
          </span>
          <span class="q-stampkey">&#8629;</span>
        </button>
      </div>
    </div>
    <div class="q-foot">
      <span class="q-hint">&#8629; confirm &middot; esc skip</span>
      <button class="q-btn q-skip" type="button" @click="skip">Skip</button>
      <button class="q-btn q-go" type="button" @click="advance">
        <span>Submit</span>
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="4" y1="12" x2="19" y2="12" /><polyline points="13 6 19 12 13 18" /></svg>
      </button>
    </div>
    <span class="q-stamp">{{ stampText }}</span>
  </div>
</template>
