<template>
<div class="overlay cmd-overlay" id="cmdOverlay">
  <div class="cmd">
    <div class="cmd-in">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7.5"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
      <input
        id="cmdInput"
        v-model="query"
        type="text"
        placeholder="Jump to a panel, start a session, switch appearance…"
        @keydown="onKey"
      />
      <span class="cmd-kbd">ESC</span>
    </div>
    <div class="cmd-list">
      <template v-for="group in groups" :key="group.label">
        <div class="cmd-group-lbl" v-if="group.items.length">{{ group.label }}</div>
        <div
          class="cmd-item"
          v-for="it in group.items"
          :key="it.id"
          :data-id="it.id"
          :class="{ sel: it.id === currentId }"
          @click="run(it)"
        >
          <span
            class="cmd-ico"
            :style="it.badge ? 'background: var(--accent-soft); color: var(--accent); font-family: var(--font-mono); font-size: 9px' : ''"
            v-html="it.icon"
          ></span>
          <span class="cmd-main">
            <span class="cmd-t">{{ it.title }}</span>
            <span class="cmd-d" v-if="it.desc">{{ it.desc }}</span>
          </span>
          <span class="cmd-k" v-if="it.key">{{ it.key }}</span>
        </div>
      </template>
      <div class="cmd-item" v-if="!rows.length">
        <span class="cmd-ico">?</span>
        <span class="cmd-main">
          <span class="cmd-t">No match</span>
          <span class="cmd-d">try “mcp”, “theme”, “session”</span>
        </span>
      </div>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ledger, llm, mcp } from '../data/panels'
import { gh } from '../data/github'
import { selectSession, sessions } from '../data/store'
import { loadSession } from '../data/wire'

/* The palette used to be static markup: fourteen hand-written rows, four of
   which only fired a "Command ran" toast because they carried no handler, and
   several whose subtitles were numbers typed once ("14 entries", "3 installed ·
   2 running", "5 sessions") that no code ever updated. Every row here is built
   from the store, so a count that changes moves here too. */

type Group = 'actions' | 'panels' | 'sessions'

interface Row {
  id: string
  group: Group
  title: string
  desc?: string
  key?: string
  /* SVG markup for a glyph, or the session's own index ("07") when badge. */
  icon: string
  badge?: boolean
  run: () => void
}

/* The shell owns the panels and the left/right rails; this is the only surface
   it exposes to Vue (see `window.__pebbleNav` in orbit/runtime.js). */
interface NavBridge {
  openSheet: (id: string) => void
  sessions: () => void
  stage: () => void
  tab: (name: string) => void
  plan: () => void
  newSession: () => void
}

function nav(): NavBridge | null {
  return (window as unknown as { __pebbleNav?: NavBridge }).__pebbleNav ?? null
}

function glyph(paths: string): string {
  return `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${paths}</svg>`
}

/* The octocat is a filled silhouette, so it cannot go through `glyph`: stroking
   it draws an outline of the outline. */
function markGlyph(size = 12): string {
  return `<svg width="${size}" height="${size}" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>`
}

const ICON = {
  plus: glyph('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>'),
  sun: glyph('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2"/>'),
  check: glyph('<polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'),
  stage: glyph('<rect x="2.5" y="4" width="19" height="13" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>'),
  search: glyph('<circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16" y2="16"/>'),
  note: glyph('<path d="M4 4h16v12l-4 4H4z"/><line x1="8" y1="9" x2="16" y2="9"/><line x1="8" y1="13" x2="13" y2="13"/>'),
  model: glyph('<rect x="4" y="4" width="16" height="16" rx="3"/><rect x="9" y="9" width="6" height="6" rx="1"/>'),
  book: glyph('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z"/>'),
  mcp: glyph('<circle cx="12" cy="12" r="3"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/>'),
  phone: glyph('<rect x="5" y="2" width="14" height="20" rx="2"/>'),
  tool: glyph('<path d="M14.7 6.3a4 4 0 0 1-5.6 5.6L3 18v3h3l6.1-6.1a4 4 0 0 1 5.6-5.6l-2.6 2.6"/>'),
  github: markGlyph(12)
}

const query = ref('')
const cursor = ref(0)

const rows = computed<Row[]>(() => {
  /* The bridge is read at click time, not here: this computed runs during the
     first render, which happens before initOrbit() installs the object, and a
     null captured into every row made the palette dead on its first open. */
  const q = query.value.trim().toLowerCase()

  const actions: Row[] = [
    {
      id: 'new-session', group: 'actions', title: 'New session',
      desc: 'start a clean transcript', key: '⌘N', icon: ICON.plus,
      run: () => { nav()?.newSession() }
    },
    {
      id: 'appearance', group: 'actions', title: 'Switch appearance',
      desc: 'daylight / nightfall · accent', key: '⌘⇧L', icon: ICON.sun,
      run: () => { nav()?.openSheet('sheet-theme') }
    },
    {
      id: 'plan', group: 'actions', title: 'Toggle task list',
      desc: 'left panel · live plan', icon: ICON.check,
      run: () => { nav()?.plan() }
    }
  ]

  /* Compact context is gone: the shell owns a compaction button beside the
     transcript, and a palette row that could rewrite the conversation by a
     stray Enter is not worth the keystroke. */
  const panels: Row[] = [
    {
      id: 'mcp', group: 'panels', title: 'MCP servers',
      desc: mcp.servers.length
        ? `${mcp.servers.length} installed · ${mcp.running} running`
        : 'no servers configured',
      icon: ICON.mcp, run: () => { nav()?.openSheet('sheet-mcp') }
    },
    {
      id: 'model', group: 'panels', title: 'Model & agents',
      desc: llm.model ? `${llm.model} · ${llm.agents.length} agents` : 'no model configured',
      icon: ICON.model,
      run: () => { nav()?.tab('llm') }
    },
    {
      id: 'ledger', group: 'panels', title: 'Experience ledger',
      desc: ledger.total ? `${ledger.total} entries` : 'nothing recorded yet',
      icon: ICON.book, run: () => { nav()?.openSheet('sheet-ledger') }
    },
    {
      id: 'tools', group: 'panels', title: 'Tools',
      desc: 'built-in tool surface', icon: ICON.tool,
      run: () => { nav()?.openSheet('sheet-tools') }
    },
    {
      id: 'notes', group: 'panels', title: 'Notes & standing note',
      desc: 'scratchpad, long-term rules', icon: ICON.note,
      run: () => { nav()?.tab('notes') }
    },
    {
      id: 'github', group: 'panels', title: 'GitHub repositories',
      desc: gh.login ? `${gh.repos.length} repositories · @${gh.login}` : 'browse and inspect repositories',
      icon: ICON.github,
      run: () => { nav()?.tab('github') }
    },
    {
      id: 'search', group: 'panels', title: 'Search messages',
      desc: 'full text in this session', icon: ICON.search,
      run: () => { nav()?.openSheet('msPop') }
    },
    {
      id: 'stage', group: 'panels', title: 'Open preview stage',
      desc: 'rendered HTML from the transcript', icon: ICON.stage,
      run: () => { nav()?.stage() }
    },
    {
      id: 'pairing', group: 'panels', title: 'Pair a device',
      desc: 'QR · single-use code', icon: ICON.phone,
      run: () => { nav()?.openSheet('sheet-pairing') }
    }
  ]

  /* Real rows from the session store, in the order the switcher shows them.
     The prototype listed two conversations that were never in the database. */
  const recent: Row[] = sessions.slice(0, 6).map(s => ({
    id: `session:${s.id}`,
    group: 'sessions' as const,
    title: s.title || 'Untitled session',
    desc: `${s.msgs || 0} msgs${s.time ? ` · ${s.time}` : ''}`,
    icon: s.index || '·',
    badge: true,
    run: () => {
      const picked = selectSession(s.id)
      if (picked) void loadSession(picked.id)
    }
  }))

  const match = (r: Row): boolean =>
    !q || `${r.title} ${r.desc ?? ''}`.toLowerCase().includes(q)

  return [...actions, ...panels, ...recent].filter(match)
})

const groups = computed(() => [
  { label: 'actions', items: rows.value.filter(r => r.group === 'actions') },
  { label: 'panels', items: rows.value.filter(r => r.group === 'panels') },
  { label: 'sessions', items: rows.value.filter(r => r.group === 'sessions') }
])

const currentId = computed(() => rows.value[cursor.value]?.id ?? '')

watch(query, () => { cursor.value = 0 })
watch(rows, () => { if (cursor.value >= rows.value.length) cursor.value = 0 })

/* The list scrolls at 52vh; eleven actions plus six sessions overflow it, and
   an arrow-key selection below the fold looks like the keys stopped working. */
watch(cursor, () => {
  const id = rows.value[cursor.value]?.id
  if (!id) return
  void nextTick(() => {
    const el = document.querySelector(`#cmdOverlay .cmd-item[data-id="${CSS.escape(id)}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  })
})

function close(): void {
  document.getElementById('cmdOverlay')?.classList.remove('open')
}

function run(it: Row): void {
  /* Close first: most rows open another overlay, and leaving this one open
     would stack two sheets. */
  close()
  query.value = ''
  cursor.value = 0
  it.run()
}

function onKey(e: KeyboardEvent): void {
  const items = rows.value
  if (!items.length) return
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    cursor.value = (cursor.value + 1) % items.length
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    cursor.value = (cursor.value - 1 + items.length) % items.length
  } else if (e.key === 'Enter') {
    e.preventDefault()
    const it = items[cursor.value]
    if (it) run(it)
  }
}

function reset(): void {
  query.value = ''
  cursor.value = 0
}

/* main.ts mounts Vue before initOrbit() runs, so the bridge object does not
   exist yet when this component mounts. The open signal travels as a DOM event
   instead, which is live whenever the palette is actually shown. */
onMounted(() => { window.addEventListener('pebble:cmd-open', reset) })
onUnmounted(() => { window.removeEventListener('pebble:cmd-open', reset) })
</script>
