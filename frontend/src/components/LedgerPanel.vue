<template>
<div class="overlay" id="sheet-ledger">
  <div class="sheet lg">
    <header class="lg-hdr">
      <span class="lg-ico">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
      </span>
      <div class="lg-title">Experience ledger<small>what the agent remembers</small></div>
      <div class="lg-total"><b>{{ ledger.total }}</b><span>entries</span></div>
      <button class="sheet-x" data-close>&#215;</button>
    </header>

    <div class="lg-dist">
      <span v-for="seg in dist" :key="seg.key" class="lg-dseg" :class="seg.cls" :style="{ '--w': seg.pct + '%' }"></span>
    </div>

    <div class="lg-tools">
      <div class="lg-search">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16" y2="16"/></svg>
        <input placeholder="Search experiences..." v-model="ledgerQuery" />
      </div>
      <div class="lg-segctl"><button :class="{ on: mode === 'ledger' }" @click="setMode('ledger')">Ledger</button><button :class="{ on: mode === 'agent' }" @click="setMode('agent')">Agent notes</button></div>
    </div>

    <div class="lg-body" v-if="mode === 'ledger'">
      <aside class="lg-list">
        <div class="lg-chips">
          <button class="lg-chip" :class="{ on: kindFilter === 'all' }" @click="kindFilter = 'all'">All<b>{{ ledger.entries.length }}</b></button>
          <button
            v-for="k in KINDS"
            :key="k.key"
            class="lg-chip"
            :class="[k.cls, { on: kindFilter === k.key }]"
            @click="kindFilter = k.key"
          >{{ k.mark }}<b>{{ countOf(k.key) }}</b></button>
        </div>

        <template v-for="g in groups" :key="g.key">
          <div class="lg-group-h">{{ g.key }}<b>{{ g.rows.length }}</b></div>
          <button class="lg-item" v-for="e in g.rows" :key="e.id" :class="{ on: e.id === selectedId }" @click="selectedId = e.id">
            <span class="lg-k" :class="kClass(e.kind)">{{ kMark(e.kind) }}</span>
            <span class="lg-main"><span class="lg-st">{{ e.text }}</span><span class="lg-meta">{{ e.kind }} · {{ e.confidence }}%</span></span>
          </button>
        </template>
        <div class="lg-none" v-if="!groups.length">no entry matches</div>
      </aside>

      <section class="lg-detail" v-if="selected">
        <div class="lg-dt-head">
          <span class="lg-badge" :class="kClass(selected.kind)">{{ selected.kind }}</span>
          <span class="lg-scope">{{ selected.scope.toUpperCase() }}</span>
          <span class="lg-age">{{ selected.age || 'now' }}</span>
        </div>
        <h4 class="lg-dt-title">{{ selected.text }}</h4>
        <div class="lg-field">
          <label>Confidence</label>
          <div class="lg-conf"><span class="lg-conf-bar"><i :style="{ '--c': selected.confidence + '%' }"></i></span><b>{{ selected.confidence }}%</b></div>
        </div>
        <div class="lg-field" v-if="selected.rationale">
          <label>Rationale</label>
          <p>{{ selected.rationale }}</p>
        </div>

        <div class="lg-field">
          <label>Applies to</label>
          <div class="lg-tags">
            <span>{{ selected.scope === 'global' ? 'all projects' : 'this project' }}</span>
            <span v-for="tag in selected.appliesTo || []" :key="tag">{{ tag }}</span>
          </div>
        </div>
        <div class="lg-actions">
          <button class="lg-del" @click="onDelete">Delete</button>
          <button class="lg-save" @click="onSave">Save</button>
        </div>
      </section>
    </div>

    <div class="lg-body agent-notes" v-else>
      <aside class="lg-list">
        <div class="lg-group-h">Select agent</div>
        <button
          class="lg-item"
          v-for="ag in agentList"
          :key="ag.name"
          :class="{ on: ag.name === agentName }"
          @click="pickAgent(ag.name)"
        >
          <span class="lg-k cons">A</span>
          <span class="lg-main">
            <span class="lg-st">{{ ag.name }}</span>
            <span class="lg-meta">{{ ag.description || 'agent' }}</span>
          </span>
        </button>
      </aside>

      <section class="lg-detail">
        <div class="lg-dt-head">
          <span class="lg-badge cons">agent</span>
          <span class="lg-scope">{{ agentName || 'no agent' }}</span>
          <span class="lg-age">{{ agentDirty ? 'unsaved' : 'synced' }}</span>
        </div>
        <div class="lg-field">
          <label>Scope</label>
          <div class="lg-segctl">
            <button :class="{ on: agentScope === 'project' }" @click="pickScope('project')">Project</button>
            <button :class="{ on: agentScope === 'user' }" @click="pickScope('user')">User</button>
          </div>
        </div>
        <div class="lg-field">
          <label>Notes carried into every turn</label>
          <textarea
            class="lm-ta"
            rows="10"
            spellcheck="false"
            v-model="agentDraft"
            placeholder="what this agent should remember..."
          ></textarea>
        </div>
        <div class="lg-actions">
          <button class="lg-del" @click="clearAgentNotes">Clear</button>
          <button class="lg-save" @click="saveAgentNotes">Save</button>
        </div>
      </section>
    </div>

    <footer class="lg-ft">
      <span class="lg-hint">{{ ledger.total }} entries · {{ globalCount }} global · synced</span>
      <button class="lg-new" @click="openNewEntry">New entry</button>
      <button class="btn btn-ghost" data-close>Close</button>
    </footer>
  </div>
</div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ledger, llm } from '../data/panels'
import { clearAgentMemory, deleteMemory, getAgentMemory, memoryStats, setAgentMemory, updateMemory } from '../data/wire'

const globalCount = computed(() => ledger.entries.filter(e => e.scope === 'global').length)

const MARKS: Record<string, string> = { constraint: 'C', preference: 'P', correction: '!', pitfall: 'Pi', reference: 'Rf', recipe: 'R' }
const CLASSES: Record<string, string> = { constraint: 'cons', preference: 'pref', correction: 'corr', pitfall: 'pit', reference: 'ref', recipe: 'reci' }

function kMark(kind: string): string { return MARKS[kind] || 'R' }
function kClass(kind: string): string { return CLASSES[kind] || 'ref' }

/* The chips and the distribution bar used to carry literals (14 / 3 / 4 / 2 / 3
   / 2 and fixed widths), so they disagreed with the list as soon as a memory was
   written or deleted. Everything below is derived from `ledger.entries`. */
const KINDS: Array<{ key: string; mark: string; cls: string }> = [
  { key: 'constraint', mark: 'C', cls: 'cons' },
  { key: 'preference', mark: 'P', cls: 'pref' },
  { key: 'correction', mark: '!', cls: 'corr' },
  { key: 'pitfall', mark: 'Pi', cls: 'pit' },
  { key: 'recipe', mark: 'R', cls: 'reci' },
  { key: 'reference', mark: 'Rf', cls: 'ref' }
]

const kindFilter = ref('all')
const ledgerQuery = ref('')

function countOf(kind: string): number {
  return ledger.entries.filter(e => e.kind === kind).length
}

const matches = computed(() => {
  const q = ledgerQuery.value.trim().toLowerCase()
  return ledger.entries.filter(e => {
    if (kindFilter.value !== 'all' && e.kind !== kindFilter.value) return false
    if (!q) return true
    return (e.text + ' ' + e.kind + ' ' + (e.scope ?? '')).toLowerCase().includes(q)
  })
})

const dist = computed(() => {
  const total = ledger.entries.length || 1
  return KINDS
    .map(k => ({ ...k, count: countOf(k.key), pct: Math.round((countOf(k.key) / total) * 100) }))
    .filter(s => s.count > 0)
})

/* Grouped by kind, the way the old memory centre listed them; the filter and the
   search narrow both the groups and their counts. */
const groups = computed(() =>
  KINDS
    .map(k => ({ ...k, rows: matches.value.filter(e => e.kind === k.key) }))
    .filter(g => g.rows.length)
)

/* The list refreshes after every write, so the selection is kept by id rather
   than by index: a delete above the current row must not move the highlight. */
const selectedId = ref(ledger.entries[0]?.id ?? '')
const selected = computed(() => ledger.entries.find(e => e.id === selectedId.value) ?? ledger.entries[0])

watch(() => ledger.entries.length, () => {
  if (!ledger.entries.some(e => e.id === selectedId.value)) {
    selectedId.value = ledger.entries[0]?.id ?? ''
  }
})

function onDelete() {
  const id = selected.value?.id
  if (id) void deleteMemory(id)
}

function onSave() {
  const row = selected.value
  if (!row) return
  void updateMemory(row.id, { statement: row.text, confidence: row.confidence / 100 })
}

function openNewEntry() {
  const el = document.getElementById('newEntryDialog')
  if (!el) return
  el.classList.add('open')
  el.setAttribute('aria-hidden', 'false')
}

/* ---- agent notes ----

   The old memory centre let you pick which agent you were editing, and which
   scope the note belongs to: a User note is injected in every project, a Project
   note only in this one. `agent-memory.get` requires both, so a single
   unlabelled textarea could only ever reach the default agent. */

const mode = ref<'ledger' | 'agent'>('ledger')
const agentList = computed(() =>
  llm.agents.map(a => ({ name: a.name, description: String((a as { description?: string }).description ?? '') }))
)
const agentName = ref('')
const agentScope = ref<'user' | 'project'>('project')
const agentDraft = ref('')
const agentSaved = ref('')
const agentDirty = computed(() => agentDraft.value !== agentSaved.value)

async function loadAgentNote(): Promise<void> {
  if (!agentName.value) return
  const text = await getAgentMemory(agentName.value, agentScope.value)
  agentDraft.value = text
  agentSaved.value = text
}

async function pickAgent(name: string): Promise<void> {
  agentName.value = name
  await loadAgentNote()
}

async function pickScope(scope: 'user' | 'project'): Promise<void> {
  agentScope.value = scope
  await loadAgentNote()
}

watch(
  () => llm.agents.length,
  () => {
    if (agentName.value) return
    const current = llm.agents.find(a => a.current) ?? llm.agents[0]
    if (!current) return
    agentName.value = current.name
    if (mode.value === 'agent') void loadAgentNote()
  },
  { immediate: true }
)

async function setMode(next: 'ledger' | 'agent'): Promise<void> {
  mode.value = next
  if (next === 'agent') await loadAgentNote()
}

async function saveAgentNotes(): Promise<void> {
  if (await setAgentMemory(agentDraft.value, agentName.value, agentScope.value)) {
    agentSaved.value = agentDraft.value
  }
}

async function clearAgentNotes(): Promise<void> {
  if (await clearAgentMemory(agentName.value, agentScope.value)) {
    agentDraft.value = ''
    agentSaved.value = ''
  }
}

/* The header total comes from memory.stats so the sheet agrees with the ledger
   even when the visible page is capped at fifty rows. */
async function pullStats(): Promise<void> {
  const st = await memoryStats()
  const total = Number(st.total ?? st.count ?? NaN)
  if (Number.isFinite(total)) ledger.total = total
}

void pullStats()
</script>
