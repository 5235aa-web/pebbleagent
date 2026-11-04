<template>
    <div class="sess-switch" id="sessSwitch">
      <button class="ss-btn" id="ssBtn" aria-haspopup="true" aria-expanded="false">
        <span class="ss-dot"></span>
        <span class="ss-title" id="ssTitle">{{ current?.title }}</span>
        <span class="ss-count">{{ current?.msgs }}</span>
        <svg class="ss-caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
      </button>

      <div class="sess-pop" id="ssPop">
        <div class="sp-top">
          <div class="sp-search">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7.5"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
            <input id="convSearch" type="text" placeholder="search sessions…" />
            <span class="sb-kbd">⌘F</span>
          </div>
          <button class="sp-new" id="newSession">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            New session
          </button>
        </div>

        <div class="sp-scroll" id="convList">
          <template v-for="group in GROUPS" :key="group">
            <div v-if="grouped[group]?.length" class="sb-label">{{ group }}</div>
            <div v-for="s in grouped[group]" :key="s.id"
                 class="conv"
                 :class="{ active: s.id === sessionState.activeId, pinned: s.pinned }"
                 data-conv tabindex="0" :data-id="s.id">
              <svg v-if="s.pinned" class="conv-pin" width="10" height="10" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.9 6.2 6.8.9-4.9 4.8 1.2 6.8L12 17.5 6 20.7l1.2-6.8L2.3 9.1l6.8-.9z"/></svg>
              <span v-else-if="s.index" class="conv-idx">{{ s.index }}</span>
              <div class="conv-body">
                <div class="conv-title">{{ s.title }}</div>
                <div class="conv-meta"><span>{{ s.msgs }} msgs</span><span class="cm-dot"></span><span>{{ s.time }}</span></div>
              </div>
              <button class="conv-more tip tip-right" data-tip="More">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></svg>
              </button>
            </div>
          </template>

          <div class="sess-empty" id="sessEmpty" v-if="!filtered.length">{{ emptyText }}</div>
        </div>

        <div class="sp-foot">
          <button class="sp-manage">Projects</button>
          <button class="sp-adopt" v-if="unassigned > 0" @click="adopt">{{ unassigned }} unassigned · adopt</button>
        </div>
      </div>
    </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { sessions, sessionState, activeSession, type Session, type SessionGroup } from '../data/store'
import { adoptUnassigned, toast, unassignedCount } from '../data/wire'

/* Sessions created before a project was chosen have no owner; the gateway can
   adopt them into the project that is open right now. */
const unassigned = ref(0)

async function pullUnassigned(): Promise<void> {
  unassigned.value = await unassignedCount()
}

async function adopt(): Promise<void> {
  const res = await adoptUnassigned(sessionState.projectPath)
  if (!res.ok) {
    toast('Adopt failed', res.error ?? 'The gateway could not adopt these sessions.', 'err')
    return
  }
  unassigned.value = 0
  /* The count comes back from the gateway, so the row reports what moved
     instead of assuming every unassigned session was adopted. */
  toast(
    res.adopted ? 'Sessions adopted' : 'Nothing to adopt',
    res.adopted
      ? `${res.adopted} unassigned sessions now belong to this project.`
      : 'No unassigned sessions were found.',
    res.adopted ? 'ok' : 'warn'
  )
}

onMounted(() => {
  void pullUnassigned()
  // The socket opens after this mounts, so the count is re-read once the
  // gateway is reachable instead of trusting the first (offline) answer.
  window.addEventListener('pebble:gateway-ready', () => { void pullUnassigned() })
})

const GROUPS: SessionGroup[] = [
  'Pinned',
  'Today',
  'Yesterday',
  'This week',
  'Last week',
  'This month',
  'Last month',
  'Earlier',
  'Last year'
]

const current = computed(() => activeSession())

/* The search box is driven by runtime.js, so the term lives in the store and
   the filter runs here. Filtering the DOM (the old way) was undone by the next
   list re-render while the term stayed in the box. */
const filtered = computed<Session[]>(() => {
  const q = sessionState.filter.trim().toLowerCase()
  if (!q) return sessions
  return sessions.filter(s => s.title.toLowerCase().includes(q))
})

const grouped = computed<Record<string, Session[]>>(() => {
  const out: Record<string, Session[]> = {}
  for (const g of GROUPS) out[g] = filtered.value.filter(s => s.group === g)
  return out
})

/* The input element itself is owned by runtime.js, so a term cleared in code
   (a project switch resets the filter) has to be written back into the box.
   The store stays the single source and the DOM follows it. */
watch(
  () => sessionState.filter,
  value => {
    const el = document.getElementById('convSearch') as HTMLInputElement | null
    if (el && el.value !== value) el.value = value
  }
)

/* An empty panel says which empty it is: still reading, could not read, or
   genuinely nothing stored. All three used to render as the same sentence. */
const emptyText = computed(() => {
  if (sessionState.filter.trim()) return 'no session matches'
  if (sessionState.listState === 'loading') return 'loading sessions…'
  if (sessionState.listState === 'failed') return 'could not read sessions'
  return 'no sessions yet'
})
</script>
