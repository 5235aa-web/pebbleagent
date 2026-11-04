<template>
        <div class="dr-pane" data-pane="llm" data-live="1">
          <div class="lm-sec">
            <div class="lm-h">agent<span>{{ llm.agents.length }} available</span></div>
            <div class="lm-agents" id="lmAgents">
              <button
                v-for="a in llm.agents"
                :key="a.name"
                class="lm-agent"
                :class="{ on: a.current }"
                @click="switchAgent(a.name)"
              >
                <span class="lm-avatar" :style="{ '--a': a.tint }">{{ a.initial }}</span>
                <span class="lm-aname">{{ a.name }}</span>
                <span class="lm-adesc">{{ a.desc }}</span>
                <span class="lm-apick">&#10003;</span>
              </button>
            </div>
          </div>

          <div class="lm-prov" id="lmProv">
            <button
              v-for="(p, i) in llm.providers"
              :key="p.slug || p.name"
              class="lm-pcard"
              :class="{ on: p.current, sel: i === effectiveSelectedIndex && !p.current }"
              @click="selectProvider(i)"
            >
              <span class="lm-brand" :style="{ '--b': 'var(--accent)' }">{{ p.name.slice(0, 1).toUpperCase() }}</span>
              <span class="lm-pname">{{ p.name }}</span>
              <span class="lm-pmeta">{{ p.models.length }} model{{ p.models.length === 1 ? '' : 's' }}{{ p.current ? ' · active' : '' }}{{ !isProviderReady(p) ? ' · not ready' : '' }}{{ isProviderReady(p) && !p.apiKey.trim() ? ' · no key' : '' }}</span>
              <span v-if="p.current" class="lm-glow"></span>
            </button>
            <button class="lm-padd" type="button" @click="createProvider">
              <span class="lm-padd-plus">+</span>
              <span class="lm-pname">new provider</span>
              <span class="lm-pmeta">base url · key · models</span>
            </button>
          </div>

          <div v-if="selectedProvider && !isProviderReady(selectedProvider)" class="lm-pnote">
            draft card — add a base url and at least one model before it can become active
          </div>
          <div v-else-if="selectedProvider && !selectedProvider.apiKey.trim()" class="lm-pnote">
            no api key — local endpoints may work; hosted endpoints will reject requests
          </div>

          <details class="lm-det">
            <summary>credentials <span>name · base url · api key</span></summary>
            <label class="lm-f"><span>name</span><input v-model="editingName" /></label>
            <label class="lm-f"><span>base url</span><input v-model="editingBaseUrl" /></label>
            <label class="lm-f"><span>api key</span><input type="password" v-model="editingApiKey" /></label>
          </details>

          <div class="lm-sec">
            <div class="lm-h">models<span>pick one</span></div>
            <div class="lm-models" id="lmModels">
              <span
                v-for="m in selectedModels"
                :key="m"
                class="lm-chip"
                :class="{ on: isActiveModel(m) }"
                @click="chooseModel(m)"
              >{{ m }}<button class="lm-mx" type="button" @click.stop="removeModel(m)">&#215;</button></span>
            </div>
            <div class="lm-madd">
              <input v-model="newModel" placeholder="add model..." @keydown.enter.prevent="addModel" @blur="addModel" />
              <button type="button" @click="addModel">+</button>
            </div>
          </div>

          <div class="lm-sec">
            <div class="lm-h">sampling<span>drag to tune</span></div>
            <div class="lm-gauge" data-k="temp" data-max="2" data-unit="">
              <span class="lm-k">temp</span>
              <div class="lm-track"><i :style="{ '--v': tempPct + '%' }"></i><b :style="{ '--v': tempPct + '%' }"></b><input type="range" min="0" max="2" step="0.1" v-model.number="selectedTemperature" @change="saveSampling" :disabled="!selectedProvider" /></div>
              <span class="lm-v">{{ selectedTemperature.toFixed(1) }}</span>
            </div>
            <div class="lm-gauge" data-k="topp" data-max="1" data-unit="">
              <span class="lm-k">top p</span>
              <div class="lm-track"><i :style="{ '--v': toppPct + '%' }"></i><b :style="{ '--v': toppPct + '%' }"></b><input type="range" min="0" max="1" step="0.05" v-model.number="selectedTopP" @change="saveSampling" :disabled="!selectedProvider" /></div>
              <span class="lm-v">{{ selectedTopP.toFixed(2) }}</span>
            </div>
          </div>

          <div class="lm-sec">
            <div class="lm-h">reasoning<span>effort</span></div>
            <div class="lm-steps" id="lmSteps">
              <button
                v-for="step in EFFORTS"
                :key="step.key"
                class="lm-step"
                :class="{ on: selectedReasoningEffort === step.key }"
                :data-e="step.key"
                :style="{ height: step.h }"
                @click="pickEffort(step.key)"
              ><i></i><span>{{ step.label }}</span></button>
            </div>
            <button class="lm-think" :class="{ on: selectedThinkingEnabled }" id="lmThink" @click="toggleThinking">
              <span class="lm-tlabel">thinking</span>
              <span class="lm-tstate">{{ selectedThinkingEnabled ? 'enabled' : 'off' }}</span>
              <span class="lm-tswitch"><i></i></span>
            </button>
          </div>

          <!-- Request shaping for OpenCode Go style gateways. These controls
               only apply to OpenCode; every other provider hides the divider
               and the block instead of showing settings that do nothing. -->
          <template v-if="isOpencode">
            <div class="lm-sep"><span>opencode-go</span></div>

            <label class="lm-f"><span>session hdr</span><input v-model="selectedSessionHeader" placeholder="x-opencode-session" /></label>

            <div class="lm-f lm-f-stack">
              <span>max effort</span>
              <div class="lm-modes">
                <button
                  v-for="e in MAX_EFFORTS"
                  :key="e"
                  :class="{ on: selectedMaxEffort === e }"
                  @click="pickMaxEffort(e)"
                >{{ e }}</button>
              </div>
            </div>

            <div class="lm-f lm-f-stack">
              <span>reasoning mode</span>
              <div class="lm-modes">
                <button
                  v-for="mode in MODES"
                  :key="mode"
                  :class="{ on: selectedReasoningMode === mode }"
                  @click="pickMode(mode)"
                >{{ mode }}</button>
              </div>
            </div>

            <details class="lm-det">
              <summary>extra headers <span>json</span></summary>
              <textarea class="lm-ta" spellcheck="false" v-model="selectedExtraHeaders"></textarea>
            </details>
          </template>

          <div class="lm-foot">
            <button class="lm-del" id="lmDel" @click="onDelete" :disabled="!selectedProvider">Delete</button>
            <button class="lm-save" id="llmSave" @click="onSave" :disabled="!selectedProvider">Save to JSON</button>
          </div>
        </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { llm, isOpenCodeProvider, isProviderReady, type LlmProvider } from '../data/panels'
import { switchAgent, switchModel, setModelParams, saveProvider, deleteProvider, addProvider, switchProvider } from '../data/wire'

const EFFORTS = [
  { key: 'low', label: 'low', h: '34%' },
  { key: 'medium', label: 'med', h: '56%' },
  { key: 'high', label: 'high', h: '78%' },
  { key: 'max', label: 'max', h: '100%' }
]
/* Two shapes only: `thinking` sends the vendor reasoning block, `effort_only`
   drops it and keeps `reasoning_effort`, which is what the OpenCode CLI sends. */
const MODES = ['thinking', 'effort_only']
const MAX_EFFORTS = ['downgrade', 'keep']

const newModel = ref('')
const selectedIndex = ref(-1)

const activeIndex = computed(() => {
  const i = llm.providers.findIndex(p => p.current)
  return i >= 0 ? i : 0
})

const effectiveSelectedIndex = computed(() => {
  if (selectedIndex.value >= 0 && selectedIndex.value < llm.providers.length) return selectedIndex.value
  return activeIndex.value
})

const selectedProvider = computed<LlmProvider | undefined>(() => llm.providers[effectiveSelectedIndex.value])
const selectedModels = computed(() => selectedProvider.value?.models ?? [])
const isOpencode = computed(() => isOpenCodeProvider(selectedProvider.value?.name || ''))
const isActiveSelection = computed(() => effectiveSelectedIndex.value === activeIndex.value)

function markDirty() {
  const p = selectedProvider.value
  if (p) p.dirty = true
}

function toast(title: string, message: string, kind: 'ok' | 'warn' | 'err' = 'warn') {
  const fn = (window as unknown as { __pebbleToast?: (t: string, m: string, k?: string) => void }).__pebbleToast
  if (fn) fn(title, message, kind)
}

const editingName = computed({
  get: () => selectedProvider.value?.name ?? '',
  set: v => { const p = selectedProvider.value; if (p) { p.name = v; markDirty() } }
})
const editingBaseUrl = computed({
  get: () => selectedProvider.value?.baseUrl ?? '',
  set: v => { const p = selectedProvider.value; if (p) { p.baseUrl = v; markDirty() } }
})
const editingApiKey = computed({
  get: () => selectedProvider.value?.apiKey ?? '',
  set: v => { const p = selectedProvider.value; if (p) { p.apiKey = v; markDirty() } }
})
const selectedTemperature = computed({
  get: () => selectedProvider.value?.temperature ?? 1,
  set: v => { const p = selectedProvider.value; if (p) { p.temperature = v; markDirty() } }
})
const selectedTopP = computed({
  get: () => selectedProvider.value?.topP ?? 1,
  set: v => { const p = selectedProvider.value; if (p) { p.topP = v; markDirty() } }
})
const selectedReasoningEffort = computed(() => selectedProvider.value?.reasoningEffort ?? 'high')
const selectedThinkingEnabled = computed(() => selectedProvider.value?.thinkingEnabled !== false)
const selectedSessionHeader = computed({
  get: () => selectedProvider.value?.sessionHeader ?? '',
  set: v => { const p = selectedProvider.value; if (p) { p.sessionHeader = v; markDirty() } }
})
const selectedReasoningMode = computed(() => selectedProvider.value?.reasoningMode ?? 'thinking')
const selectedMaxEffort = computed(() => selectedProvider.value?.maxEffort ?? 'downgrade')
const selectedExtraHeaders = computed({
  get: () => selectedProvider.value?.extraHeaders ?? '{}',
  set: v => { const p = selectedProvider.value; if (p) { p.extraHeaders = v; markDirty() } }
})

const tempPct = computed(() => Math.min(100, (selectedTemperature.value / 2) * 100))
const toppPct = computed(() => Math.min(100, selectedTopP.value * 100))

function isActiveModel(model: string): boolean {
  return isActiveSelection.value && model === llm.model
}

function pickEffort(key: string) {
  const p = selectedProvider.value
  if (!p) return
  p.reasoningEffort = key
  p.dirty = true
  void setModelParams(effectiveSelectedIndex.value)
}

function toggleThinking() {
  const p = selectedProvider.value
  if (!p) return
  p.thinkingEnabled = !p.thinkingEnabled
  p.dirty = true
  void setModelParams(effectiveSelectedIndex.value)
}

function saveSampling() {
  if (!selectedProvider.value) return
  void setModelParams(effectiveSelectedIndex.value)
}

function pickMode(mode: string) {
  const p = selectedProvider.value
  if (!p) return
  p.reasoningMode = mode
  p.dirty = true
  void saveProvider(effectiveSelectedIndex.value)
}

function pickMaxEffort(e: string) {
  const p = selectedProvider.value
  if (!p) return
  p.maxEffort = e
  p.dirty = true
  void saveProvider(effectiveSelectedIndex.value)
}

function selectProvider(index: number) {
  const provider = llm.providers[index]
  if (!provider) return
  selectedIndex.value = index
  if (index === activeIndex.value) return
  if (!isProviderReady(provider)) {
    toast('Draft selected', 'Add a base url and at least one model. The active provider is unchanged.', 'warn')
    return
  }
  const model = provider.models.includes(llm.model) ? llm.model : (provider.models[0] ?? '')
  void switchProvider(index, model)
}

async function createProvider() {
  const idx = await addProvider()
  if (idx !== null) selectedIndex.value = idx
}

function chooseModel(model: string) {
  const index = effectiveSelectedIndex.value
  const provider = selectedProvider.value
  if (!provider) return
  if (index === activeIndex.value) {
    void switchModel(model)
    return
  }
  if (!isProviderReady(provider)) {
    toast('Provider not ready', 'Add a base url and api key before activating a model.', 'warn')
    return
  }
  void switchProvider(index, model)
}

function addModel() {
  const name = newModel.value.trim()
  const index = effectiveSelectedIndex.value
  const provider = selectedProvider.value
  if (!name) return
  if (!provider) {
    newModel.value = ''
    return
  }
  newModel.value = ''
  if (/\s/.test(name)) {
    toast('Invalid model id', 'Model ids must not contain spaces. Use the provider\'s exact id.', 'err')
    return
  }
  if (provider.models.includes(name)) {
    chooseModel(name)
    return
  }
  const owned = provider.models.includes(llm.model)
  const previousModels = [...provider.models]
  const previousDirty = provider.dirty
  provider.models = [...provider.models, name]
  provider.dirty = true
  void saveProvider(index).then(ok => {
    if (!ok) {
      /* The backend rejected the write: the chip must not stay behind as if it
         had been saved. Keep any other dirty credential edits for retry. */
      const current = llm.providers[index]
      if (current) {
        current.models = previousModels
        current.dirty = previousDirty
      }
      return
    }
    const saved = llm.providers[index]
    if (!saved || saved.dirty) return
    if (!isProviderReady(saved)) {
      toast('Saved as draft', 'Add the base url/key before this provider can become active.', 'warn')
      return
    }
    if (index === activeIndex.value) {
      if (!owned) void switchModel(name)
    } else if (effectiveSelectedIndex.value === index) {
      void switchProvider(index, name)
    }
  })
}

function removeModel(name: string) {
  const index = effectiveSelectedIndex.value
  const provider = selectedProvider.value
  if (!provider) return
  const wasCurrent = index === activeIndex.value && llm.model === name
  const previousModels = [...provider.models]
  const previousDirty = provider.dirty
  provider.models = provider.models.filter(m => m !== name)
  provider.dirty = true
  const next = provider.models[0] ?? ''
  void saveProvider(index).then(ok => {
    if (!ok) {
      const current = llm.providers[index]
      if (current) {
        current.models = previousModels
        current.dirty = previousDirty
      }
      return
    }
    const saved = llm.providers[index]
    if (!saved || saved.dirty) return
    if (wasCurrent && index === activeIndex.value && next) void switchModel(next)
  })
}

function onSave() {
  const index = effectiveSelectedIndex.value
  const provider = selectedProvider.value
  if (!provider) return
  void saveProvider(index).then(ok => {
    if (!ok) return
    if (effectiveSelectedIndex.value !== index) return
    const saved = llm.providers[index]
    if (!saved || saved.dirty || !isProviderReady(saved) || saved.current) return
    const model = saved.models.includes(llm.model) ? llm.model : (saved.models[0] ?? '')
    void switchProvider(index, model)
  })
}

function onDelete() {
  const provider = selectedProvider.value
  if (!provider) return
  if (!window.confirm(`Delete provider "${provider.name}"? This removes its API key, base URL and models from llm_providers.json.`)) return
  void deleteProvider(effectiveSelectedIndex.value).then(ok => {
    if (ok) selectedIndex.value = -1
  })
}
</script>
