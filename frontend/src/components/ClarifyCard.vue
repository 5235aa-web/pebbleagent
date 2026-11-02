<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ClarifyQuestion } from '../data/panels'

const props = defineProps<{ requestId: string; questions: ClarifyQuestion[] }>()
const emit = defineEmits<{
  (e: 'submit', answers: Record<string, string>, settle: (ok: boolean) => void): void
  (e: 'skip', settle: (ok: boolean) => void): void
}>()

const step = ref(0)
const picked = ref<string[][]>(props.questions.map(() => []))
const custom = ref<string[]>(props.questions.map(() => ''))
const done = ref(false)
const sending = ref(false)
const failed = ref(false)
const stampText = ref('Answered')

const current = computed<ClarifyQuestion | undefined>(() => props.questions[step.value])
const total = computed(() => props.questions.length)
const stepNo = computed(() => String(step.value + 1).padStart(2, '0'))
const pickedHere = computed(() => picked.value[step.value] ?? [])
const customHere = computed(() => custom.value[step.value] ?? '')
const isPicked = (label: string) => pickedHere.value.includes(label)

function choose(label: string) {
  if (done.value || sending.value || !current.value) return
  const row = picked.value[step.value]
  if (!row) return
  if (current.value.multiple) {
    const at = row.indexOf(label)
    if (at > -1) row.splice(at, 1)
    else row.push(label)
  } else {
    // A single-choice answer is either a preset or a typed one, never both.
    row.splice(0, row.length, label)
    custom.value[step.value] = ''
  }
}

/** Typing on a single-choice question takes over from the preset selection. */
function typeCustom(ev: Event) {
  if (done.value || sending.value) return
  const idx = step.value
  custom.value[idx] = (ev.target as HTMLTextAreaElement).value
  if (!current.value?.multiple && custom.value[idx].trim()) picked.value[idx] = []
}

function collect(): Record<string, string> {
  const out: Record<string, string> = {}
  props.questions.forEach((q, i) => {
    const parts = [...(picked.value[i] ?? [])]
    const typed = (custom.value[i] ?? '').trim()
    if (typed) parts.push(typed)
    if (parts.length) out[q.prompt] = parts.join(', ')
  })
  return out
}

function advance() {
  if (done.value || sending.value) return
  if (step.value < total.value - 1) {
    step.value += 1
    failed.value = false
    return
  }
  const answers = collect()
  failed.value = false
  sending.value = true
  emit('submit', answers, (ok: boolean) => {
    sending.value = false
    if (ok) {
      done.value = true
      return
    }
    // Nothing was recorded, so the card goes back to being answerable.
    failed.value = true
  })
}

function skip() {
  if (done.value || sending.value) return
  failed.value = false
  sending.value = true
  emit('skip', (ok: boolean) => {
    sending.value = false
    if (!ok) {
      failed.value = true
      return
    }
    done.value = true
    stampText.value = 'Skipped'
  })
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
      <div class="q-custom">
        <span class="q-custom-label">or write your own</span>
        <textarea
          class="q-custom-input"
          :value="customHere"
          rows="2"
          spellcheck="false"
          :disabled="done || sending"
          :placeholder="current?.multiple ? 'Type your own answer, it is added after the options you picked' : 'Type your own answer instead of picking an option'"
          @input="typeCustom"
        ></textarea>
        <span class="q-custom-note">{{ customHere.trim() ? (current?.multiple ? 'added after your picks' : 'replaces the picked option') : 'no option fits? write it here' }}</span>
      </div>
    </div>
    <div class="q-foot">
      <span class="q-hint" :class="{ bad: failed }">
        <template v-if="failed">not sent &middot; try again</template>
        <template v-else-if="sending">sending &hellip;</template>
        <template v-else>&#8629; confirm &middot; esc skip</template>
      </span>
      <button class="q-btn q-skip" type="button" :disabled="sending" @click="skip">Skip</button>
      <button class="q-btn q-go" type="button" :disabled="sending" @click="advance">
        <span>{{ sending ? 'Sending' : 'Submit' }}</span>
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="4" y1="12" x2="19" y2="12" /><polyline points="13 6 19 12 13 18" /></svg>
      </button>
    </div>
    <span class="q-stamp">{{ stampText }}</span>
  </div>
</template>
