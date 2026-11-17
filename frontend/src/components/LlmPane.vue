<template>
  <div class="dr-pane llm-pane" data-pane="llm" data-live="1">
    <!-- Agent selector -->
    <section class="lm-card-sec">
      <div class="lm-sec-head">
        <span class="lm-sec-title">Agent</span>
        <span class="lm-sec-meta">{{ llm.agents.length }} available</span>
      </div>
      <div class="lm-agents" id="lmAgents">
        <button
          v-for="a in llm.agents"
          :key="a.name"
          type="button"
          class="lm-agent"
          :class="{ on: a.current }"
          @click="switchAgent(a.name)"
        >
          <span class="lm-avatar" :style="{ '--a': a.tint }">{{ a.initial }}</span>
          <span class="lm-agent-info">
            <span class="lm-aname">{{ a.name }}</span>
            <span class="lm-adesc">{{ a.desc }}</span>
          </span>
          <span v-if="a.current" class="lm-apick" aria-hidden="true">&#10003;</span>
        </button>
      </div>
    </section>

    <!-- Provider cards -->
    <section class="lm-card-sec">
      <div class="lm-sec-head">
        <span class="lm-sec-title-wrap">
          <span class="lm-sec-title">Providers</span>
          <HelpTip title="OpenCode Go Setup Guide">
            <p>To use an <strong>OpenCode Go</strong> subscription, create a provider card and fill these fields:</p>
            <ul>
              <li><strong>Name:</strong> Set to <code>opencode-go</code> (or <code>opencode</code> / <code>OpenCode Go</code>) to unlock the OpenCode Go settings below.</li>
              <li><strong>Base URL:</strong> <code>https://opencode.ai/inference/go/openai/v1</code></li>
              <li><strong>API Key:</strong> Paste your OpenCode Go API key.</li>
              <li><strong>Models:</strong> Add your subscription model IDs (e.g. <code>glm-5.3</code>, <code>kimi-k3.1</code>, <code>minimax-m3.1</code>).</li>
              <li><strong>OpenCode Go section:</strong> Set <strong>Session Header</strong> to <code>x-opencode-session</code>, choose <code>effort_only</code> (recommended) or <code>thinking</code> for Reasoning Mode, and click <strong>Save &amp; Activate</strong>.</li>
              <li><strong>Context ring tip:</strong> In <strong>Usage → models · rates &amp; windows</strong>, add each model with only its <code>ctx</code> size and leave rates blank.</li>
            </ul>
          </HelpTip>
        </span>
        <button class="lm-add-btn" type="button" @click="createProvider">+ New</button>
      </div>
      <div class="lm-prov" id="lmProv">
        <button
          v-for="(p, i) in llm.providers"
          :key="p.slug || p.name"
          type="button"
          class="lm-pcard"
          :class="{ on: p.current, sel: i === effectiveSelectedIndex && !p.current }"
          @click="selectProvider(i)"
        >
          <div class="lm-pcard-top">
            <span class="lm-brand" :style="{ '--b': p.current ? 'var(--accent)' : 'var(--muted)' }">{{ (p.name || '?').slice(0, 1).toUpperCase() }}</span>
            <span v-if="p.current" class="lm-pbadge active">active</span>
            <span v-else-if="!isProviderReady(p)" class="lm-pbadge draft">draft</span>
            <span v-else-if="!p.apiKey.trim()" class="lm-pbadge warn">no key</span>
          </div>
          <span class="lm-pname">{{ p.name || 'Unnamed' }}</span>
          <span class="lm-pmeta">{{ p.models.length }} model{{ p.models.length === 1 ? '' : 's' }}{{ p.dirty ? ' · unsaved' : '' }}</span>
          <span v-if="p.current" class="lm-glow"></span>
        </button>
      </div>
    </section>

    <!-- Selected provider workspace -->
    <section v-if="selectedProvider" class="lm-panel">
      <header class="lm-panel-head">
        <div class="lm-panel-title">
          <strong>{{ selectedProvider.name || 'New provider' }}</strong>
          <span v-if="selectedProvider.current" class="lm-pill ok">Active</span>
          <span v-else-if="!isProviderReady(selectedProvider)" class="lm-pill warn">Draft</span>
          <span v-else class="lm-pill">Standby</span>
          <span v-if="selectedProvider.dirty" class="lm-dirty-dot" title="Unsaved changes"></span>
        </div>
        <button
          type="button"
          class="lm-cred-toggle"
          @click="credentialsOpen = !credentialsOpen"
        >
          {{ credentialsOpen ? 'Hide config' : 'Credentials' }}
        </button>
      </header>

      <div v-if="!isProviderReady(selectedProvider)" class="lm-pnote">
        Add a base URL and at least one model before this provider can become active.
      </div>
      <div v-else-if="!selectedProvider.apiKey.trim()" class="lm-pnote">
        No API key set — local endpoints may work; hosted endpoints will reject requests.
      </div>

      <!-- Credentials (expanded automatically for drafts / missing keys) -->
      <div v-if="credentialsOpen" class="lm-cred-box">
        <label class="lm-field">
          <span class="lm-field-lbl">Name</span>
          <input v-model="editingName" placeholder="e.g. deepseek, opencode-go" spellcheck="false" />
        </label>
        <label class="lm-field">
          <span class="lm-field-lbl">Base URL</span>
          <input v-model="editingBaseUrl" placeholder="https://api.example.com/v1" spellcheck="false" />
        </label>
        <label class="lm-field">
          <span class="lm-field-lbl">API Key</span>
          <div class="lm-key-wrap">
            <input
              :type="showApiKey ? 'text' : 'password'"
              v-model="editingApiKey"
              placeholder="sk-..."
              spellcheck="false"
            />
            <button
              type="button"
              class="lm-key-eye"
              :title="showApiKey ? 'Hide key' : 'Show key'"
              @click="showApiKey = !showApiKey"
            >{{ showApiKey ? 'Hide' : 'Show' }}</button>
          </div>
        </label>
      </div>

      <!-- Models -->
      <div class="lm-block">
        <div class="lm-sec-head">
          <span class="lm-sec-title">Models</span>
          <span class="lm-sec-meta">{{ selectedModels.length ? 'click to switch' : 'none yet' }}</span>
        </div>
        <div class="lm-models" id="lmModels" v-if="selectedModels.length">
          <span
            v-for="m in selectedModels"
            :key="m"
            class="lm-chip"
            :class="{ on: isActiveModel(m) }"
            @click="chooseModel(m)"
          >
            <span class="lm-chip-dot" v-if="isActiveModel(m)"></span>
            <span class="lm-chip-txt">{{ m }}</span>
            <button class="lm-mx" type="button" title="Remove model" @click.stop="removeModel(m)">&#215;</button>
          </span>
        </div>
        <div class="lm-madd">
          <input v-model="newModel" placeholder="Add model id (e.g. Qwen4-Max)…" spellcheck="false" @keydown.enter.prevent="addModel" @blur="addModel" />
          <button type="button" @click="addModel">+</button>
        </div>
      </div>

      <!-- Sampling -->
      <div class="lm-block">
        <div class="lm-sec-head">
          <span class="lm-sec-title">Sampling</span>
          <span class="lm-sec-meta">drag to tune</span>
        </div>
        <div class="lm-gauge" data-k="temp" data-max="2" data-unit="">
          <span class="lm-k">Temp</span>
          <div class="lm-track">
            <i :style="{ '--v': tempPct + '%' }"></i>
            <b :style="{ '--v': tempPct + '%' }"></b>
            <input type="range" min="0" max="2" step="0.1" v-model.number="selectedTemperature" @change="saveSampling" :disabled="!selectedProvider" />
          </div>
          <span class="lm-v">{{ selectedTemperature.toFixed(1) }}</span>
        </div>
        <div class="lm-gauge" data-k="topp" data-max="1" data-unit="">
          <span class="lm-k">Top P</span>
          <div class="lm-track">
            <i :style="{ '--v': toppPct + '%' }"></i>
            <b :style="{ '--v': toppPct + '%' }"></b>
            <input type="range" min="0" max="1" step="0.05" v-model.number="selectedTopP" @change="saveSampling" :disabled="!selectedProvider" />
          </div>
          <span class="lm-v">{{ selectedTopP.toFixed(2) }}</span>
        </div>
      </div>

      <!-- Reasoning -->
      <div class="lm-block">
        <div class="lm-sec-head">
          <span class="lm-sec-title">Reasoning</span>
          <span class="lm-scope-pill">{{ reasoningScopeLabel }}</span>
        </div>
        <button type="button" class="lm-think" :class="{ on: selectedThinkingEnabled }" id="lmThink" @click="toggleThinking">
          <span class="lm-tlabel">Thinking</span>
          <span class="lm-tstate">{{ selectedThinkingEnabled ? 'Enabled' : 'Off' }}</span>
          <span class="lm-tswitch"><i></i></span>
        </button>
        <div class="lm-steps" :class="{ 'is-off': !selectedThinkingEnabled }" id="lmSteps">
          <button
            v-for="step in EFFORTS"
            :key="step.key"
            type="button"
            class="lm-step"
            :class="{ on: selectedReasoningEffort === step.key }"
            :data-e="step.key"
            @click="pickEffort(step.key)"
          >
            <span class="lm-step-bar"><i :style="{ height: step.h }"></i></span>
            <span class="lm-step-lbl">{{ step.label }}</span>
          </button>
        </div>
      </div>

      <!-- Request shaping for OpenCode Go style gateways. Shown only when
           the selected provider belongs to the OpenCode family. -->
      <div v-if="isOpencode" class="lm-opencode">
        <div class="lm-sec-head">
          <span class="lm-sec-title">OpenCode Go</span>
          <span class="lm-opencode-tag">request shaping</span>
        </div>

        <label class="lm-field">
          <span class="lm-field-lbl">Session Header</span>
          <input v-model="selectedSessionHeader" placeholder="x-opencode-session" spellcheck="false" />
        </label>

        <div class="lm-seg-group">
          <div class="lm-seg-head">
            <span>Max Effort</span>
            <small>{{ selectedMaxEffort === 'keep' ? 'send max verbatim' : 'clamp max to high' }}</small>
          </div>
          <div class="lm-modes">
            <button
              v-for="e in MAX_EFFORTS"
              :key="e"
              type="button"
              :class="{ on: selectedMaxEffort === e }"
              @click="pickMaxEffort(e)"
            >{{ e }}</button>
          </div>
        </div>

        <div class="lm-seg-group">
          <div class="lm-seg-head">
            <span>Reasoning Mode</span>
            <small>{{ selectedReasoningMode === 'effort_only' ? 'reasoning_effort only' : 'include thinking block' }}</small>
          </div>
          <div class="lm-modes">
            <button
              v-for="mode in MODES"
              :key="mode"
              type="button"
              :class="{ on: selectedReasoningMode === mode }"
              @click="pickMode(mode)"
            >{{ mode }}</button>
          </div>
        </div>

        <div class="lm-seg-group">
          <div class="lm-seg-head">
            <span>Extra Headers (JSON)</span>
            <small :class="{ 'is-err': Boolean(extraHeadersError) }">{{ extraHeadersError || 'valid json' }}</small>
          </div>
          <textarea
            class="lm-ta"
            spellcheck="false"
            placeholder='{"User-Agent": "PebbleAgent/1.0"}'
            v-model="selectedExtraHeaders"
          ></textarea>
        </div>
      </div>

      <footer class="lm-foot">
        <button class="lm-del" id="lmDel" type="button" @click="onDelete" :disabled="!selectedProvider">Delete</button>
        <button class="lm-save" id="llmSave" type="button" @click="onSave" :disabled="!selectedProvider">
          <span v-if="selectedProvider.dirty" class="lm-save-dot"></span>
          {{ saveButtonLabel }}
        </button>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { llm, isOpenCodeProvider, isProviderReady, resolveModelThinking, type LlmProvider } from '../data/panels'
import { switchAgent, switchModel, setModelParams, setModelThinking, saveProvider, deleteProvider, addProvider, switchProvider } from '../data/wire'
import { askConfirm } from '../data/confirm'
import HelpTip from './HelpTip.vue'

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
const credentialsOpen = ref(false)
const showApiKey = ref(false)

const activeIndex = computed(() => {
  const i = llm.providers.findIndex(p => p.current)
  return i >= 0 ? i : 0
})

/* Keep the panel's selected card aligned when the active provider changes from
   outside (for example, from the composer's upward model picker). */
watch(activeIndex, () => {
  selectedIndex.value = -1
})

const effectiveSelectedIndex = computed(() => {
  if (selectedIndex.value >= 0 && selectedIndex.value < llm.providers.length) return selectedIndex.value
  return activeIndex.value
})

const selectedProvider = computed<LlmProvider | undefined>(() => llm.providers[effectiveSelectedIndex.value])
const selectedModels = computed(() => selectedProvider.value?.models ?? [])
const isOpencode = computed(() => isOpenCodeProvider(selectedProvider.value?.name || ''))
const isActiveSelection = computed(() => effectiveSelectedIndex.value === activeIndex.value)

/* Auto-open credentials when inspecting a draft or unauthenticated card so a
   new user immediately sees Base URL & API Key without hunting in a collapse. */
watch(
  [effectiveSelectedIndex, () => selectedProvider.value?.slug],
  () => {
    showApiKey.value = false
    const p = selectedProvider.value
    credentialsOpen.value = Boolean(p && (!isProviderReady(p) || !p.apiKey.trim()))
  },
  { immediate: true }
)

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
const activeScopedModel = computed(() => {
  const p = selectedProvider.value
  if (!p || !isActiveSelection.value || !llm.model) return ''
  return p.models.includes(llm.model) ? llm.model : ''
})
const reasoningScopeLabel = computed(() => activeScopedModel.value || 'provider default')
const selectedReasoningEffort = computed(() => {
  const p = selectedProvider.value
  if (!p) return 'high'
  if (activeScopedModel.value) {
    return resolveModelThinking(p, activeScopedModel.value).reasoningEffort
  }
  return p.reasoningEffort ?? 'high'
})
const selectedThinkingEnabled = computed(() => {
  const p = selectedProvider.value
  if (!p) return true
  if (activeScopedModel.value) {
    return resolveModelThinking(p, activeScopedModel.value).thinkingEnabled
  }
  return p.thinkingEnabled !== false
})
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

const extraHeadersError = computed(() => {
  if (!isOpencode.value) return ''
  try {
    const parsed = JSON.parse(selectedExtraHeaders.value || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return 'must be a JSON object'
    }
    return ''
  } catch {
    return 'invalid JSON'
  }
})

const saveButtonLabel = computed(() => {
  const p = selectedProvider.value
  if (!p) return 'Save'
  if (isProviderReady(p) && !p.current) return 'Save & Activate'
  return 'Save'
})

const tempPct = computed(() => Math.min(100, (selectedTemperature.value / 2) * 100))
const toppPct = computed(() => Math.min(100, selectedTopP.value * 100))

function isActiveModel(model: string): boolean {
  return isActiveSelection.value && model === llm.model
}

function pickEffort(key: string) {
  const p = selectedProvider.value
  if (!p) return
  if (activeScopedModel.value) {
    void setModelThinking(effectiveSelectedIndex.value, activeScopedModel.value, { reasoningEffort: key })
    return
  }
  p.reasoningEffort = key
  p.dirty = true
  void setModelParams(effectiveSelectedIndex.value)
}

function toggleThinking() {
  const p = selectedProvider.value
  if (!p) return
  if (activeScopedModel.value) {
    void setModelThinking(effectiveSelectedIndex.value, activeScopedModel.value, {
      thinkingEnabled: !selectedThinkingEnabled.value
    })
    return
  }
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
  if (idx !== null) {
    selectedIndex.value = idx
    credentialsOpen.value = true
  }
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

async function onDelete() {
  const provider = selectedProvider.value
  if (!provider) return
  /* window.confirm does not follow the shell's theme or sizing, so the delete
     decision goes through the shared confirmation sheet. */
  const confirmed = await askConfirm({
    title: 'Delete provider?',
    body: `"${provider.name}" is removed from llm_providers.json, including its API key, base URL and models.`,
    confirmLabel: 'Delete',
    danger: true,
    hint: 'this cannot be undone'
  })
  if (!confirmed) return
  void deleteProvider(effectiveSelectedIndex.value).then(ok => {
    if (ok) selectedIndex.value = -1
  })
}
</script>
