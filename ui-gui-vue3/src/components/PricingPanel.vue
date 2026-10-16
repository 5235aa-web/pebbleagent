<script setup lang="ts">
import { ref, watch } from 'vue'
import { useGatewayStore } from '../stores/gateway'
import Tooltip from './Tooltip.vue'
import ConfirmDialog from './ConfirmDialog.vue'

interface PriceBlock { input_per_million: number; cache_hit_per_million: number; output_per_million: number }
type RawEntry = Record<string, unknown> & { display_name?: string; provider?: string; currency?: string; pricing?: PriceBlock; pricing_peak?: PriceBlock; pricing_offpeak?: PriceBlock }
interface EditRow { name: string; raw: RawEntry; base: string[]; peak: string[] | null; off: string[] | null }

// 价格三列列头（与后端 input_per_million / cache_hit_per_million / output_per_million 一一对应）
const FIELD_LABELS = ['cache miss', 'cache hit', 'output']

const g = useGatewayStore()
const o = ref(false)
const btnRef = ref<HTMLButtonElement | null>(null)
const panelStyle = ref<Record<string, string>>({})
const rows = ref<EditRow[]>([])
const defCur = ref('CNY')
const loading = ref(false)
const saving = ref(false)
const saved = ref(false)
const errMsg = ref('')
const confirmDelIdx = ref(-1)
const adding = ref(false)
const newName = ref('')
const newRef = ref<HTMLInputElement | null>(null)

function blk2row(b: PriceBlock | null | undefined): string[] | null {
  if (!b) return null
  const s = (v: unknown) => (typeof v === 'number' && isFinite(v)) ? String(v) : ''
  return [s(b.input_per_million), s(b.cache_hit_per_million), s(b.output_per_million)]
}

function toggle() {
  if (!o.value && btnRef.value) {
    const r = btnRef.value.getBoundingClientRect()
    panelStyle.value = { left: Math.max(8, Math.min(r.left, window.innerWidth - 416)) + 'px' }
  }
  o.value = !o.value
}

async function load() {
  loading.value = true
  errMsg.value = ''
  try {
    const r = await g.request<{ pricing?: { models?: Record<string, RawEntry>; default_currency?: string } }>('config.get', { key: 'pricing' })
    const models = r?.pricing?.models || {}
    defCur.value = r?.pricing?.default_currency || 'CNY'
    rows.value = Object.entries(models).map(([name, e]) => ({
      name,
      raw: e,
      base: blk2row(e.pricing) || ['', '', ''],
      peak: blk2row(e.pricing_peak),
      off: blk2row(e.pricing_offpeak),
    }))
  } catch { errMsg.value = 'failed to load pricing' }
  loading.value = false
}

watch(() => o.value, v => { if (v) load() })

function startAdd() { adding.value = true; newName.value = ''; setTimeout(() => newRef.value?.focus(), 50) }
function confirmAdd() {
  const n = newName.value.trim()
  if (!n) return
  if (rows.value.some(r => r.name === n)) { errMsg.value = `model '${n}' already exists`; return }
  rows.value.unshift({ name: n, raw: {}, base: ['0', '0', '0'], peak: null, off: null })
  adding.value = false
  errMsg.value = ''
}
function doDelete() {
  if (confirmDelIdx.value >= 0) rows.value.splice(confirmDelIdx.value, 1)
  confirmDelIdx.value = -1
}

// 输入框值写入编辑态（arr 为 null 时忽略——对应 peak/off 不存在的行）
function setVal(arr: string[] | null, i: number, e: Event) {
  if (arr) arr[i] = (e.target as HTMLInputElement).value
}

function parseBlock(vals: string[]): { ok: boolean; block?: PriceBlock; err?: string } {
  const keys = ['input_per_million', 'cache_hit_per_million', 'output_per_million']
  const out: Record<string, number> = {}
  for (let i = 0; i < 3; i++) {
    const s = (vals[i] || '').trim()
    const n = s === '' ? NaN : Number(s)
    if (!isFinite(n) || n < 0) return { ok: false, err: `${keys[i]} must be a non-negative number` }
    out[keys[i]] = n
  }
  return { ok: true, block: out as unknown as PriceBlock }
}

async function save() {
  if (saving.value) return
  errMsg.value = ''
  if (!rows.value.length) { errMsg.value = 'no models to save'; return }
  const models: Record<string, Record<string, unknown>> = {}
  for (const r of rows.value) {
    const b = parseBlock(r.base)
    if (!b.ok) { errMsg.value = `${r.name}: ${b.err}`; return }
    const entry: Record<string, unknown> = {
      ...r.raw,
      display_name: (r.raw.display_name as string) || r.name,
      provider: (r.raw.provider as string) || '',
      currency: (r.raw.currency as string) || defCur.value,
      pricing: b.block,
    }
    if (r.peak) { const p = parseBlock(r.peak); if (!p.ok) { errMsg.value = `${r.name} peak: ${p.err}`; return } entry.pricing_peak = p.block }
    if (r.off) { const p = parseBlock(r.off); if (!p.ok) { errMsg.value = `${r.name} offpeak: ${p.err}`; return } entry.pricing_offpeak = p.block }
    models[r.name] = entry
  }
  saving.value = true
  try {
    const res = await g.request<{ ok?: boolean; error?: string }>('pricing.save', { models, default_currency: defCur.value })
    if (res?.ok) {
      saved.value = true
      setTimeout(() => saved.value = false, 2000)
      window.dispatchEvent(new CustomEvent('pricing-updated'))
      await load()
    } else {
      errMsg.value = res?.error || 'save failed'
    }
  } catch (e) { errMsg.value = 'save failed: ' + String(e) }
  saving.value = false
}
</script>
<template>
<Tooltip text="Model Pricing" position="bottom">
  <button ref="btnRef" class="mp-btn" :class="{active:o}" @click="toggle"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg></button>
</Tooltip>
<div v-if="o" class="mp-overlay" @click="o=false">
  <div class="mp-panel" :style="panelStyle" @click.stop>
    <div class="mp-hdr">
      <span class="mp-title">Model Pricing</span>
      <span class="mp-sub">per 1M tokens &middot; {{defCur}}</span>
      <button class="mp-add" @click="startAdd" aria-label="Add model">+</button>
    </div>
    <div v-if="adding" class="mp-add-row">
      <input ref="newRef" class="mp-inp" v-model="newName" placeholder="model name..." @keydown.enter="confirmAdd" @keydown.escape="adding=false"/>
      <button class="mp-add-ok" @click="confirmAdd">add</button>
    </div>
    <div class="mp-list">
      <div v-if="loading" class="mp-empty">Loading...</div>
      <div v-else-if="!rows.length" class="mp-empty">no models configured</div>
      <div v-for="(r, i) in rows" :key="r.name" class="mp-row">
        <div class="mp-row-hdr">
          <span class="mp-name">{{r.name}}</span>
          <span v-if="r.raw.provider" class="mp-prov">{{r.raw.provider}}</span>
          <button class="mp-del" @click="confirmDelIdx=i" aria-label="Delete model">&times;</button>
        </div>
        <div class="mp-pr"><span class="mp-tag">base</span>
          <div v-for="(lb, j) in FIELD_LABELS" :key="lb" class="mp-f">
            <span class="mp-flb">{{lb}}</span>
            <input class="mp-inp" type="number" step="any" min="0" :value="r.base[j]" @input="setVal(r.base, j, $event)"/>
          </div>
        </div>
        <div v-if="r.peak" class="mp-pr"><span class="mp-tag">peak</span>
          <div v-for="(lb, j) in FIELD_LABELS" :key="lb" class="mp-f">
            <span class="mp-flb">{{lb}}</span>
            <input class="mp-inp" type="number" step="any" min="0" :value="r.peak[j]" @input="setVal(r.peak, j, $event)"/>
          </div>
        </div>
        <div v-if="r.off" class="mp-pr"><span class="mp-tag">off</span>
          <div v-for="(lb, j) in FIELD_LABELS" :key="lb" class="mp-f">
            <span class="mp-flb">{{lb}}</span>
            <input class="mp-inp" type="number" step="any" min="0" :value="r.off[j]" @input="setVal(r.off, j, $event)"/>
          </div>
        </div>
      </div>
    </div>
    <div class="mp-foot">
      <span class="mp-err">{{errMsg}}</span>
      <div class="mp-foot-r">
        <button class="mp-save" :disabled="saving" @click="save">{{saving?'saving...':'Save to JSON'}}</button>
        <span v-if="saved" class="mp-saved">saved</span>
      </div>
    </div>
  </div>
</div>
<ConfirmDialog :open="confirmDelIdx>=0" title="Delete model" :message="`Delete model '${rows[confirmDelIdx]?.name || ''}' from pricing?`" confirmLabel="Delete" cancelLabel="Cancel" :danger="true" @confirm="doDelete" @cancel="confirmDelIdx=-1"/>
</template>
