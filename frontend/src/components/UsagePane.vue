<template>
  <!-- Pricing rows are rendered from the gateway table, not the prototype copy. -->
        <div class="dr-pane" data-pane="usage">
          <div class="usage-hero">
            <div class="usage-ring">
              <svg width="74" height="74" viewBox="0 0 74 74">
                <circle class="t" cx="37" cy="37" r="30" pathLength="100"></circle>
                <circle class="f" cx="37" cy="37" r="30" pathLength="100" :style="{ strokeDashoffset: ringOffset }"></circle>
              </svg>
              <div class="lbl"><b>{{ usage.contextLimit ? usage.contextPct + '%' : '—' }}</b><span>context</span></div>
            </div>
            <div class="usage-info">
              <div class="usage-big">{{ fmtCost(usage.costToday, usage.currency) }} <small>today</small></div>
              <div class="usage-sub">{{ usage.tokensIn.toLocaleString() }} in / {{ usage.tokensOut.toLocaleString() }} out · {{ usage.calls }} calls</div>
              <div class="usage-tag">
                <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
                {{ priced ? 'priced' : 'unpriced' }} · {{ tier }} tier
              </div>
            </div>
          </div>

          <div class="stat-grid">
            <div class="stat-box"><div class="k">input tokens</div><div class="v">{{ fmtTokens(usage.tokensIn) }} <small>· {{ inShare }}%</small></div></div>
            <div class="stat-box"><div class="k">output tokens</div><div class="v">{{ fmtTokens(usage.tokensOut) }} <small>· {{ outShare }}%</small></div></div>
            <div class="stat-box"><div class="k">live rate</div><div class="v">{{ liveRate }} <small>tok/s</small></div></div>
            <div class="stat-box"><div class="k">session cost</div><div class="v">{{ fmtCost(usage.sessionCost, usage.currency) }}</div></div>
          </div>

          <div class="section-h">call history</div>
          <div class="bill-row" v-for="(row, i) in usage.history" :key="i">
            <span class="bill-dot" :style="row.tint ? 'background:' + row.tint : ''"></span>
            <span class="bill-model">{{ row.model }}</span>
            <span class="bill-tok">{{ fmtTokens(row.tokensIn) }} / {{ fmtTokens(row.tokensOut) }}</span>
            <span class="bill-cost">{{ fmtCost(row.cost, usage.currency) }}</span>
          </div>

          <div class="section-h">pricing · per 1M tokens
            <span class="sh-note" id="pxCur">usd</span>
          </div>

          <div class="px-bar">
            <div class="px-seg" id="pxSeg">
              <button :class="{ on: pricingPeriod === 'base' }" @click="pricingPeriod = 'base'">base</button>
              <button :class="{ on: pricingPeriod === 'peak' }" @click="pricingPeriod = 'peak'">peak</button>
              <button :class="{ on: pricingPeriod === 'off' }" @click="pricingPeriod = 'off'">off</button>
            </div>
            <button class="px-add" id="pxAdd" @click="addModel">+ model</button>
          </div>

          <div class="px-list" id="pxList">
            <article
              v-for="row in pricingRows"
              :key="row.name"
              class="px-card"
              :data-name="row.name"
            >
              <header class="px-head">
                <span class="px-dot" :style="{ background: row.tint }"></span>
                <span class="px-model">{{ row.name }}</span>
                <span class="px-prov">{{ row.vendor }}</span>
                <button class="px-del" data-tip="Delete" @click="dropModel(row.name)">&#215;</button>
              </header>
              <div v-if="row.block" class="px-cells">
                <label class="px-cell"><span>cache miss</span><input :value="row.block.input" @change="setRate(row.name, 'input', $event)" /></label>
                <label class="px-cell"><span>cache hit</span><input :value="row.block.cache_hit" @change="setRate(row.name, 'cache_hit', $event)" /></label>
                <label class="px-cell"><span>output</span><input :value="row.block.output" @change="setRate(row.name, 'output', $event)" /></label>
              </div>
              <div v-else class="px-cells">
                <span class="px-norate">no {{ pricingPeriod }} rate</span>
              </div>
            </article>
            <div v-if="!pricingRows.length" class="px-norate px-empty">no models in the pricing table</div>
          </div>

          <div class="px-foot">
            <span class="px-hint">{{ pricingHint }}</span>
            <button class="px-save" id="pxSave" :disabled="pricingSaving" @click="saveTable">{{ pricingSaving ? 'Saving…' : 'Save' }}</button>
          </div>
        </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { usage as usageState, llm, fmtTokens, fmtCost } from '../data/panels'
import { streamState } from '../data/stream'
import { formatRate, getConfig, savePricing } from '../data/wire'

const usage = usageState

/* The arc is a stroke over pathLength=100, so the dash offset is the fill in
   percent. It has to be driven by the same number the label prints: the offset
   was a fixed 33 in CSS, so a session at 4% still drew a two-thirds ring. An
   unreported window leaves it empty instead. */
const ringOffset = computed(() => (usage.contextLimit ? 100 - usage.contextPct : 100))
const total = computed(() => usage.tokensIn + usage.tokensOut || 1)
const inShare = computed(() => Math.round((usage.tokensIn / total.value) * 100))
const outShare = computed(() => 100 - inShare.value)

/* Pricing comes from the table the gateway loaded out of model_pricing.json. A
   model with no entry is genuinely unpriced rather than "standard"; a model that
   carries a `pricing_tier_high` block is billed on two tiers. */
const pricingOf = computed(() => {
  const table = llm.pricing as Record<string, { pricing_tier_high?: unknown }> | undefined
  const name = streamState.model || llm.model || ''
  const entry = name && table ? table[name] : undefined
  return { known: Boolean(entry), tiered: Boolean(entry?.pricing_tier_high) }
})
const priced = computed(() => pricingOf.value.known)
const tier = computed(() => (pricingOf.value.tiered ? 'tiered' : 'standard'))

/* Same source as the island: one measurement, two readouts. */
const liveRate = computed(() => formatRate())

/* ---- pricing table ----

   `model_pricing.json` holds one entry per model with a block per billing
   period, and `pricing.save` validates those blocks verbatim. The pane
   therefore edits the raw table from `config.get` instead of a flattened list,
   which is what keeps `pricing_peak` / `pricing_tier_high` from being dropped
   on save. */

type RateBlock = {
  input_per_million?: number
  cache_hit_per_million?: number
  output_per_million?: number
}
type PricingEntry = {
  pricing?: RateBlock
  pricing_peak?: RateBlock
  pricing_offpeak?: RateBlock
  pricing_tier_high?: RateBlock
  provider?: string
  vendor?: string
}

const pricingData = ref<{ models: Record<string, PricingEntry>; default_currency: string }>({
  models: {},
  default_currency: 'usd'
})
const pricingPeriod = ref<'base' | 'peak' | 'off'>('base')
const pricingSaving = ref(false)
const pricingHint = ref('edits apply to the active period')
const TINTS = ['var(--accent)', 'var(--teal)', 'var(--gold)']

const pricingRows = computed(() =>
  Object.keys(pricingData.value.models || {}).map((name, i) => {
    const entry = pricingData.value.models[name] || {}
    const block =
      pricingPeriod.value === 'base'
        ? entry.pricing
        : pricingPeriod.value === 'peak'
          ? entry.pricing_peak
          : entry.pricing_offpeak
    return {
      name,
      vendor: String(entry.provider || entry.vendor || ''),
      tint: TINTS[i % TINTS.length],
      block: block
        ? {
            input: Number(block.input_per_million ?? 0).toFixed(2),
            cache_hit: Number(block.cache_hit_per_million ?? 0).toFixed(2),
            output: Number(block.output_per_million ?? 0).toFixed(2)
          }
        : null
    }
  })
)

let pricingRetries = 0

/** The pane mounts before the socket handshake finishes, so an empty answer is
    retried for a few seconds instead of leaving the table blank for the rest of
    the session. */
async function loadPricing(): Promise<void> {
  const raw = (await getConfig('pricing')) as
    | {
        pricing?: { models?: Record<string, PricingEntry>; default_currency?: string }
        models?: Record<string, PricingEntry>
        default_currency?: string
      }
    | null
  // `config.get` answers `{ pricing: {...}, period, cn_holidays }`, so the table
  // can arrive either at the root or under `pricing`.
  const payload = raw?.pricing ?? raw
  if (!payload?.models || !Object.keys(payload.models).length) {
    if (pricingRetries < 20) {
      pricingRetries += 1
      window.setTimeout(() => { void loadPricing() }, 500)
    }
    return
  }
  pricingRetries = 0
  pricingData.value = {
    models: payload.models,
    default_currency: String(payload.default_currency || 'usd')
  }
}

/** The block the active period edits, created on first touch so a model that
    only has a base rate can gain a peak one. */
function blockOf(entry: PricingEntry): RateBlock {
  if (pricingPeriod.value === 'base') return (entry.pricing ||= {})
  if (pricingPeriod.value === 'peak') return (entry.pricing_peak ||= {})
  return (entry.pricing_offpeak ||= {})
}

function setRate(name: string, key: 'input' | 'cache_hit' | 'output', ev: Event): void {
  const entry = pricingData.value.models[name]
  if (!entry) return
  const value = parseFloat((ev.target as HTMLInputElement).value)
  blockOf(entry)[(key + '_per_million') as keyof RateBlock] = Number.isFinite(value) ? value : 0
}

function addModel(): void {
  let n = 1
  while (pricingData.value.models['new-model-' + n]) n++
  pricingData.value.models['new-model-' + n] = { pricing: { input_per_million: 0, cache_hit_per_million: 0, output_per_million: 0 } }
}

function dropModel(name: string): void {
  delete pricingData.value.models[name]
}

async function saveTable(): Promise<void> {
  if (pricingSaving.value) return
  pricingSaving.value = true
  pricingHint.value = 'saving…'
  const ok = await savePricing(
    pricingData.value.models as unknown as Record<string, unknown>,
    pricingData.value.default_currency
  )
  pricingSaving.value = false
  pricingHint.value = ok ? 'saved · applies to the next call' : 'save failed'
  if (ok) void loadPricing()
  window.setTimeout(() => {
    pricingHint.value = 'edits apply to the active period'
  }, 3200)
}

onMounted(() => {
  void loadPricing()
})
</script>
