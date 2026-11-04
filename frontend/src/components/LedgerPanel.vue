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
      <div class="lg-search" v-if="mode === 'ledger'">
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
        <div class="lg-none" v-if="!groups.length">{{ emptyText }}</div>
      </aside>

      <section class="lg-detail" v-if="selected">
        <div class="lg-dt-head">
          <span class="lg-badge" :class="kClass(selected.kind)">{{ selected.kind }}</span>
          <span class="lg-scope">{{ selected.scope.toUpperCase() }}</span>
          <span class="lg-age">{{ selected.age || 'now' }}</span>
        </div>
        <div class="lg-field">
          <label>Statement</label>
          <textarea class="lg-edit" rows="3" spellcheck="false" v-model="ledgerDraft"></textarea>
        </div>
        <div class="lg-field">
          <label>Confidence</label>
          <div class="lg-conf">
            <input
              class="lg-range"
              type="range"
              min="0"
              max="100"
              step="1"
              v-model.number="ledgerConf"
              :style="{ '--c': ledgerConf + '%' }"
            />
            <b>{{ ledgerConf }}%</b>
          </div>
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
          <span class="lg-age">{{ noteStatus }}</span>
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
          <button class="lg-del" :disabled="!agentName" @click="clearAgentNotes">Clear</button>
          <button class="lg-save" :disabled="!agentName" @click="saveAgentNotes">Save</button>
        </div>
      </section>
    </div>

    <footer class="lg-ft">
      <span class="lg-hint">{{ matches.length }} shown · {{ ledger.total }} stored · {{ globalCount }} global in view</span>
      <button class="lg-new" @click="openNewEntry">New entry</button>
      <button class="btn btn-ghost" data-close>Close</button>
    </footer>
  </div>
</div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ledger, llm } from '../data/panels'
import { clearAgentMemory, deleteMemory, getAgentMemory, memoryStats, setAgentMemory, toast, updateMemory } from '../data/wire'
import { askConfirm } from '../data/confirm'

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

/* The detail pane edits a draft, not the store row. `memory.update` is the only
   thing allowed to change the ledger, and an abandoned edit must leave no trace
   in the list. */
const ledgerDraft = ref('')
const ledgerConf = ref(100)
const ledgerSavedText = ref('')
const ledgerSavedConf = ref(100)
const ledgerDirty = computed(() => ledgerDraft.value !== ledgerSavedText.value || ledgerConf.value !== ledgerSavedConf.value)

function syncLedgerDraft(): void {
  const row = selected.value
  ledgerDraft.value = row?.text ?? ''
  ledgerConf.value = row?.confidence ?? 100
  ledgerSavedText.value = ledgerDraft.value
  ledgerSavedConf.value = ledgerConf.value
}

watch(() => selected.value?.id ?? '', syncLedgerDraft, { immediate: true })

/* Three ways to be empty, told apart: a failed read is not "no entries". */
const emptyText = computed(() => {
  if (ledger.state === 'loading') return 'loading…'
  if (ledger.state === 'failed') return 'could not read the ledger'
  if (ledgerQuery.value.trim() || kindFilter.value !== 'all') return 'no entry matches'
  return 'no entries yet'
})

async function onDelete(): Promise<void> {
  const row = selected.value
  if (!row) return
  const text = row.text.length > 90 ? row.text.slice(0, 90) + '…' : row.text
  const ok = await askConfirm({
    title: 'Delete entry?',
    body: `"${text}" is removed from the experience ledger.`,
    confirmLabel: 'Delete',
    cancelLabel: 'Keep',
    danger: true,
    hint: 'this cannot be undone'
  })
  if (!ok) return
  if (await deleteMemory(row.id)) {
    toast('Deleted', 'The entry was removed from the ledger.', 'ok')
    return
  }
  toast('Could not delete', 'The gateway refused the request.', 'err')
}

async function onSave(): Promise<void> {
  const row = selected.value
  if (!row) return
  const text = ledgerDraft.value.trim()
  if (!text) {
    toast('Cannot save', 'The statement is empty.', 'warn')
    return
  }
  if (!ledgerDirty.value) {
    toast('Nothing to save', 'No changes since the last save.', 'warn')
    return
  }
  const ok = await updateMemory(row.id, { statement: text, confidence: ledgerConf.value / 100 })
  if (!ok) {
    toast('Could not save', 'The gateway refused the update.', 'err')
    return
  }
  ledgerSavedText.value = text
  ledgerSavedConf.value = ledgerConf.value
  toast('Saved', 'The entry was updated.', 'ok')
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
/* A failed read and an empty note are different answers: only one of them may
   clear the textarea. */
const noteError = ref(false)
let noteSeq = 0

const noteStatus = computed(() => {
  if (!agentName.value) return 'no agent'
  if (noteError.value) return 'could not read'
  return agentDirty.value ? 'unsaved' : 'synced'
})

async function loadAgentNote(): Promise<void> {
  const name = agentName.value
  if (!name) return
  /* Only the newest request may write. Two quick agent picks used to resolve out
     of order, leaving the name on one agent and the text on another. */
  const seq = ++noteSeq
  noteError.value = false
  const text = await getAgentMemory(name, agentScope.value)
  if (seq !== noteSeq) return
  if (text === null) {
    /* A failed read must not leave the previous agent's text in the box: the
       next save would write it under this agent's name. Clearing it and saying
       so is honest; the status line keeps reading "could not read". */
    noteError.value = true
    agentDraft.value = ''
    agentSaved.value = ''
    return
  }
  agentDraft.value = text
  agentSaved.value = text
}

/* Switching agent or scope replaces the textarea, so unsaved work has to be
   acknowledged first. It used to be discarded without a word. */
async function confirmDiscard(): Promise<boolean> {
  if (!agentDirty.value) return true
  return askConfirm({
    title: 'Discard unsaved note?',
    body: `The ${agentScope.value} note for ${agentName.value} has changes that were not saved.`,
    confirmLabel: 'Discard',
    cancelLabel: 'Keep editing',
    danger: true
  })
}

async function pickAgent(name: string): Promise<void> {
  if (name === agentName.value) return
  if (!(await confirmDiscard())) return
  agentName.value = name
  await loadAgentNote()
}

async function pickScope(scope: 'user' | 'project'): Promise<void> {
  if (scope === agentScope.value) return
  if (!(await confirmDiscard())) return
  agentScope.value = scope
  await loadAgentNote()
}

watch(
  () => llm.agents.map(a => a.name).join('\u0000'),
  () => {
    const names = llm.agents.map(a => a.name)
    /* An agent that disappeared must not stay selected: saving would write to a
       type nothing renders any more. */
    if (agentName.value && names.includes(agentName.value)) return
    const current = llm.agents.find(a => a.current) ?? llm.agents[0]
    if (!current) {
      agentName.value = ''
      return
    }
    agentName.value = current.name
    if (mode.value === 'agent') void loadAgentNote()
  },
  { immediate: true }
)

async function setMode(next: 'ledger' | 'agent'): Promise<void> {
  if (next === mode.value) return
  /* Coming back reloads the note, so leaving has the same rule as switching. */
  if (!(await confirmDiscard())) return
  mode.value = next
  if (next === 'agent') await loadAgentNote()
}

async function saveAgentNotes(): Promise<void> {
  if (!agentName.value) {
    toast('Cannot save', 'Select an agent first.', 'warn')
    return
  }
  /* After a failed read the box is blank, and saving blank would wipe a note the
     gateway still holds. Writing blind is worse than asking for a reload. */
  if (noteError.value) {
    toast('Cannot save', 'The note could not be read. Pick the agent again to reload it.', 'warn')
    return
  }
  if (await setAgentMemory(agentDraft.value, agentName.value, agentScope.value)) {
    agentSaved.value = agentDraft.value
    noteError.value = false
    toast('Saved', `The ${agentScope.value} note for ${agentName.value} was updated.`, 'ok')
    return
  }
  toast('Could not save', 'The gateway refused the note.', 'err')
}

async function clearAgentNotes(): Promise<void> {
  if (!agentName.value) {
    toast('Cannot clear', 'Select an agent first.', 'warn')
    return
  }
  const ok = await askConfirm({
    title: 'Clear agent note?',
    body: `The ${agentScope.value} note for ${agentName.value} is deleted.`,
    confirmLabel: 'Clear',
    cancelLabel: 'Keep',
    danger: true,
    hint: 'this cannot be undone'
  })
  if (!ok) return
  if (await clearAgentMemory(agentName.value, agentScope.value)) {
    agentDraft.value = ''
    agentSaved.value = ''
    toast('Cleared', `The ${agentScope.value} note for ${agentName.value} was removed.`, 'ok')
    return
  }
  toast('Could not clear', 'The gateway refused the request.', 'err')
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
