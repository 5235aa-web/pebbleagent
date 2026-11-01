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
              v-for="p in llm.providers"
              :key="p.slug || p.name"
              class="lm-pcard"
              :class="{ on: p.current }"
            >
              <span class="lm-brand" :style="{ '--b': 'var(--accent)' }">{{ p.name.slice(0, 1).toUpperCase() }}</span>
              <span class="lm-pname">{{ p.name }}</span>
              <span class="lm-pmeta">{{ p.models.length }} model{{ p.models.length === 1 ? '' : 's' }}{{ p.current ? ' · active' : '' }}</span>
              <span v-if="p.current" class="lm-glow"></span>
            </button>
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
                v-for="m in activeModels"
                :key="m"
                class="lm-chip"
                :class="{ on: m === llm.model }"
                @click="switchModel(m)"
              >{{ m }}<button class="lm-mx" type="button" @click.stop="removeModel(m)">&#215;</button></span>
            </div>
            <div class="lm-madd">
              <input v-model="newModel" placeholder="add model..." @keydown.enter.prevent="addModel" />
              <button type="button" @click="addModel">+</button>
            </div>
          </div>

          <div class="lm-sec">
            <div class="lm-h">sampling<span>drag to tune</span></div>
            <div class="lm-gauge" data-k="temp" data-max="2" data-unit="">
              <span class="lm-k">temp</span>
              <div class="lm-track"><i :style="{ '--v': tempPct + '%' }"></i><b :style="{ '--v': tempPct + '%' }"></b><input type="range" min="0" max="2" step="0.1" v-model.number="llm.temperature" @change="setModelParams" /></div>
              <span class="lm-v">{{ llm.temperature.toFixed(1) }}</span>
            </div>
            <div class="lm-gauge" data-k="topp" data-max="1" data-unit="">
              <span class="lm-k">top p</span>
              <div class="lm-track"><i :style="{ '--v': toppPct + '%' }"></i><b :style="{ '--v': toppPct + '%' }"></b><input type="range" min="0" max="1" step="0.05" v-model.number="llm.topP" @change="setModelParams" /></div>
              <span class="lm-v">{{ llm.topP.toFixed(2) }}</span>
            </div>
            <div class="lm-gauge" data-k="maxtok" data-max="32000" data-unit="k">
              <span class="lm-k">max tok</span>
              <div class="lm-track"><i :style="{ '--v': tokPct + '%' }"></i><b :style="{ '--v': tokPct + '%' }"></b><input type="range" min="512" max="32000" step="512" v-model.number="llm.maxTokens" @change="setModelParams" /></div>
              <span class="lm-v">{{ llm.maxTokens.toLocaleString() }}</span>
            </div>
          </div>

          <div class="lm-sec">
            <div class="lm-h">reasoning<span>effort</span></div>
            <div class="lm-steps" id="lmSteps">
              <button
                v-for="step in EFFORTS"
                :key="step.key"
                class="lm-step"
                :class="{ on: llm.reasoningEffort === step.key }"
                :data-e="step.key"
                :style="{ height: step.h }"
                @click="pickEffort(step.key)"
              ><i></i><span>{{ step.label }}</span></button>
            </div>
            <button class="lm-think" :class="{ on: llm.thinkingEnabled }" id="lmThink" @click="toggleThinking">
              <span class="lm-tlabel">thinking</span>
              <span class="lm-tstate">{{ llm.thinkingEnabled ? 'enabled' : 'off' }}</span>
              <span class="lm-tswitch"><i></i></span>
            </button>
          </div>

          <!-- Request shaping for OpenCode Go style gateways. Everything below is
               optional and ignored by plain OpenAI-compatible endpoints. -->
          <div class="lm-sep"><span>opencode-go</span></div>

          <label class="lm-f"><span>session hdr</span><input v-model="llm.sessionHeader" placeholder="x-opencode-session" /></label>

          <div class="lm-f lm-f-stack">
            <span>max effort</span>
            <div class="lm-modes">
              <button
                v-for="e in MAX_EFFORTS"
                :key="e"
                :class="{ on: llm.maxEffort === e }"
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
                :class="{ on: llm.reasoningMode === mode }"
                @click="pickMode(mode)"
              >{{ mode }}</button>
            </div>
          </div>

          <details class="lm-det">
            <summary>extra headers <span>json</span></summary>
            <textarea class="lm-ta" spellcheck="false" v-model="llm.extraHeaders"></textarea>
          </details>

          <div class="lm-foot">
            <button class="lm-del" id="lmDel" @click="onDelete">Delete</button>
            <button class="lm-save" id="llmSave" @click="onSave">Save to JSON</button>
          </div>
        </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { llm } from '../data/panels'
import { switchAgent, switchModel, setModelParams, saveProvider, deleteProvider } from '../data/wire'

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

const activeIndex = computed(() => {
  const i = llm.providers.findIndex(p => p.current)
  return i >= 0 ? i : 0
})

const activeModels = computed(() => llm.providers[activeIndex.value]?.models ?? [])

const tempPct = computed(() => Math.min(100, (llm.temperature / 2) * 100))
const toppPct = computed(() => Math.min(100, llm.topP * 100))
const tokPct = computed(() => Math.min(100, ((llm.maxTokens - 512) / (32000 - 512)) * 100))

const editingName = computed({
  get: () => llm.providers[activeIndex.value]?.name ?? '',
  set: v => { const p = llm.providers[activeIndex.value]; if (p) p.name = v }
})
const editingBaseUrl = computed({
  get: () => llm.providers[activeIndex.value]?.baseUrl ?? '',
  set: v => { const p = llm.providers[activeIndex.value]; if (p) p.baseUrl = v }
})
const editingApiKey = computed({
  get: () => llm.providers[activeIndex.value]?.apiKey ?? '',
  set: v => { const p = llm.providers[activeIndex.value]; if (p) p.apiKey = v }
})

function pickEffort(key: string) {
  llm.reasoningEffort = key
  setModelParams()
}

function toggleThinking() {
  llm.thinkingEnabled = !llm.thinkingEnabled
  setModelParams()
}

function pickMode(mode: string) {
  llm.reasoningMode = mode
  saveProvider(activeIndex.value)
}

function pickMaxEffort(e: string) {
  llm.maxEffort = e
  saveProvider(activeIndex.value)
}

function addModel() {
  const name = newModel.value.trim()
  const provider = llm.providers[activeIndex.value]
  if (!name || !provider || provider.models.includes(name)) {
    newModel.value = ''
    return
  }
  provider.models = [...provider.models, name]
  newModel.value = ''
  saveProvider(activeIndex.value)
}

function removeModel(name: string) {
  const provider = llm.providers[activeIndex.value]
  if (!provider) return
  provider.models = provider.models.filter(m => m !== name)
  if (llm.model === name) llm.model = provider.models[0] ?? ''
  saveProvider(activeIndex.value)
}

function onSave() {
  saveProvider(activeIndex.value)
}

function onDelete() {
  deleteProvider(activeIndex.value)
}
</script>
