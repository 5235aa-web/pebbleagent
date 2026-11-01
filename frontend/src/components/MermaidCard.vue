<template>
  <div class="mmd" :class="{ 'is-error': state === 'error', 'is-live': streaming }">
    <header class="mmd-bar">
      <span class="mmd-mark">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <rect x="3" y="3" width="7" height="6" rx="1.5" />
          <rect x="14" y="15" width="7" height="6" rx="1.5" />
          <path d="M6.5 9v4.5a2 2 0 0 0 2 2H14" />
        </svg>
      </span>
      <span class="mmd-kind">{{ kindLabel }}</span>
      <span class="mmd-dot"></span>
      <span class="mmd-st" :class="state">{{ statusLabel }}</span>
      <span class="mmd-spacer"></span>
      <button class="mmd-act" type="button" @click="showSource = !showSource">{{ showSource ? 'Hide source' : 'Source' }}</button>
      <button class="mmd-act" type="button" @click="copySource">{{ copied ? 'Copied' : 'Copy' }}</button>
      <button class="mmd-act" type="button" :disabled="state !== 'success'" @click="savePng">PNG</button>
    </header>

    <div class="mmd-stage">
      <div v-if="streaming" class="mmd-wait">
        <span class="mmd-pulse"></span>
        <span>drawing the diagram</span>
      </div>
      <div v-else-if="state === 'error'" class="mmd-fail">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <circle cx="12" cy="12" r="9" />
          <line x1="12" y1="8" x2="12" y2="13" />
          <line x1="12" y1="16.5" x2="12" y2="16.5" />
        </svg>
        <span>{{ errorText }}</span>
      </div>
      <div v-show="!streaming" ref="canvas" class="mmd-svg"></div>
    </div>

    <pre v-if="showSource" class="mmd-src">{{ code }}</pre>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{ code: string; id: string; streaming?: boolean }>()

const canvas = ref<HTMLDivElement | null>(null)
const state = ref<'idle' | 'loading' | 'success' | 'error'>('idle')
const errorText = ref('')
const showSource = ref(false)
const copied = ref(false)

let renderSeq = 0
let timer = 0
let mermaid: typeof import('mermaid').default | null = null

/* The diagram kind is the first token of the source, which is also what the
   renderer dispatches on. Naming it beats showing a raw keyword. */
const KIND_NAMES: Record<string, string> = {
  flowchart: 'flowchart',
  graph: 'flowchart',
  sequencediagram: 'sequence',
  classdiagram: 'class',
  statediagram: 'state',
  'statediagram-v2': 'state',
  erdiagram: 'ER',
  journey: 'journey',
  gantt: 'gantt',
  pie: 'pie',
  quadrantchart: 'quadrant',
  requirementdiagram: 'requirement',
  gitgraph: 'git graph',
  mindmap: 'mindmap',
  timeline: 'timeline',
  sankey: 'sankey',
  'sankey-beta': 'sankey',
  xychart: 'xy chart',
  'xychart-beta': 'xy chart',
  block: 'block',
  'block-beta': 'block',
  packet: 'packet',
  'packet-beta': 'packet',
  architecture: 'architecture',
  'architecture-beta': 'architecture',
  kanban: 'kanban',
  radar: 'radar',
  'radar-beta': 'radar',
  treemap: 'treemap'
}

const kindLabel = computed(() => {
  const first = props.code.trim().split('\n')[0]?.trim().split(/\s+/)[0]?.toLowerCase() ?? ''
  return KIND_NAMES[first] || first || 'diagram'
})

const statusLabel = computed(() => {
  if (props.streaming) return 'streaming'
  if (state.value === 'loading') return 'rendering'
  if (state.value === 'error') return 'render failed'
  if (state.value === 'success') return 'ready'
  return 'idle'
})

/* Models write Chinese punctuation around labels; mermaid's parser only accepts
   the ASCII forms. Same normalisation as the shell so both renders agree. */
function prepare(source: string): string {
  return source.replace(/[\u201c\u201d]/g, "'").replace(/\u300a/g, '(').replace(/\u300b/g, ')')
}

/* Theme values have to be read from the live document: mermaid bakes colours
   into the SVG, so a fixed palette would ignore the accent and the dark theme. */
function themeVariables(): Record<string, string> {
  const css = getComputedStyle(document.documentElement)
  const read = (name: string, fallback: string): string => css.getPropertyValue(name).trim() || fallback
  const accent = read('--accent', '#d64e2a')
  const ink = read('--ink', '#17130f')
  return {
    background: read('--surface', '#fffdf9'),
    primaryColor: read('--accent-soft', 'rgba(214,78,42,.14)'),
    primaryTextColor: ink,
    primaryBorderColor: accent,
    secondaryColor: read('--surface-2', '#f4ede2'),
    tertiaryColor: read('--surface-3', '#efe7da'),
    lineColor: read('--line-strong', '#cbbfae'),
    textColor: ink,
    mainBkg: read('--surface-2', '#f4ede2'),
    nodeBorder: accent,
    clusterBkg: read('--surface-3', '#efe7da'),
    clusterBorder: read('--line', '#e8dfd1'),
    edgeLabelBackground: read('--surface', '#fffdf9'),
    titleColor: ink,
    actorBkg: read('--surface-2', '#f4ede2'),
    actorBorder: accent,
    actorTextColor: ink,
    signalColor: read('--line-strong', '#cbbfae'),
    signalTextColor: ink,
    labelBoxBkgColor: read('--surface-2', '#f4ede2'),
    labelBoxBorderColor: accent,
    noteBkgColor: read('--accent-soft-2', 'rgba(214,78,42,.07)'),
    noteBorderColor: accent,
    fontFamily: read('--font-mono', 'ui-monospace, monospace')
  }
}

async function loadMermaid(): Promise<typeof import('mermaid').default> {
  if (mermaid) return mermaid
  const mod = await import('mermaid')
  mermaid = mod.default
  mermaid.initialize({
    startOnLoad: false,
    theme: 'base',
    securityLevel: 'strict',
    fontFamily: 'inherit',
    flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' },
    sequence: { useMaxWidth: true },
    gantt: { useMaxWidth: true },
    themeVariables: themeVariables()
  })
  return mermaid
}

async function render(): Promise<void> {
  const host = canvas.value
  if (!host || props.streaming || !props.code.trim()) return
  state.value = 'loading'
  errorText.value = ''
  const seq = ++renderSeq
  const safeId = props.id.replace(/[^\w-]/g, '')
  try {
    const m = await loadMermaid()
    // A second render can start while the first is still parsing; the later one
    // owns the host so a stale SVG never lands on top of a fresh one.
    if (seq !== renderSeq) return
    const { svg } = await m.render(`mmd-${safeId}-${seq}`, prepare(props.code))
    if (seq !== renderSeq) return
    host.innerHTML = svg
    state.value = 'success'
  } catch (err) {
    document.getElementById(`dmmd-${safeId}-${seq}`)?.remove()
    if (seq !== renderSeq) return
    errorText.value = String((err as Error)?.message || err).split('\n')[0].slice(0, 160)
    state.value = 'error'
  }
}

function schedule(): void {
  window.clearTimeout(timer)
  timer = window.setTimeout(() => { void render() }, 120)
}

watch(() => [props.code, props.streaming], schedule, { deep: false })

onMounted(() => { void render() })
onBeforeUnmount(() => { window.clearTimeout(timer); renderSeq += 1 })

async function copySource(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.code)
    copied.value = true
    window.setTimeout(() => { copied.value = false }, 1500)
  } catch { /* clipboard unavailable */ }
}

async function savePng(): Promise<void> {
  const svgEl = canvas.value?.querySelector('svg')
  if (!svgEl) return
  const box = svgEl.getBoundingClientRect()
  const scale = 2
  const cv = document.createElement('canvas')
  cv.width = Math.max(1, Math.round(box.width * scale))
  cv.height = Math.max(1, Math.round(box.height * scale))
  const ctx = cv.getContext('2d')
  if (!ctx) return
  const css = getComputedStyle(document.documentElement)
  ctx.fillStyle = css.getPropertyValue('--surface').trim() || '#ffffff'
  ctx.fillRect(0, 0, cv.width, cv.height)
  const data = new XMLSerializer().serializeToString(svgEl)
  const url = URL.createObjectURL(new Blob([data], { type: 'image/svg+xml;charset=utf-8' }))
  try {
    const img = new Image()
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve()
      img.onerror = () => reject(new Error('image load failed'))
      img.src = url
    })
    ctx.drawImage(img, 0, 0, cv.width, cv.height)
    const a = document.createElement('a')
    a.download = kindLabel.value.replace(/\s+/g, '-') + '.png'
    a.href = cv.toDataURL('image/png')
    a.click()
  } catch { /* keep the card as it is */ } finally {
    URL.revokeObjectURL(url)
  }
}
</script>
