<template>
        <div class="dr-pane" data-pane="context">
          <div class="ctx-bar">
            <i v-for="(g, i) in segments" :key="i" :class="g.cls" :style="{ width: g.pct + '%' }"></i>
          </div>
          <div class="ctx-legend">
            <div class="ctx-lr" v-for="(g, i) in segments" :key="i"><i :style="{ background: g.color }"></i><span class="k">{{ g.label }}</span><span class="v">{{ g.value }}</span></div>
          </div>

          <div class="section-h">active agent</div>
          <div class="snap">
            <span class="snap-ico" style="color:var(--accent)">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/></svg>
            </span>
            <div class="snap-main">
              <div class="snap-title">{{ agentName }}</div>
              <div class="snap-sub">{{ agentSub }}</div>
            </div>
          </div>

          <div class="section-h">skills loaded</div>
          <div>
            <span class="skill-chip" :class="{ pre: !isTurnLoaded(k) }" v-for="k in skills" :key="k">{{ k }}</span>
          </div>
          <div class="section-sub" v-if="!hasUserSkills">no user skills · ask the agent to add one to ~/.pebble/skills</div>

          <div class="section-h">tool · tokens this turn</div>
          <div class="change-row" v-for="(t, i) in visibleTools" :key="i">
            <span class="ch-ico" :class="t.ico">{{ t.mark }}</span>
            <span class="ch-path">{{ t.text }}</span>
            <span class="ch-num">{{ t.time }}</span>
          </div>
          <button v-if="hiddenToolCount" class="ch-more" @click="toolsExpanded = !toolsExpanded">
            {{ toolsExpanded ? 'collapse' : `+${hiddenToolCount} more` }}
          </button>

          <div class="section-h">memory</div>
          <div class="snap" style="cursor:default">
            <span class="snap-ico">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
            </span>
            <div class="snap-main">
              <div class="snap-title">{{ context.ledgerTotal }} ledger entries</div>
              <div class="snap-sub">{{ ledgerSub }}</div>
            </div>
          </div>
        </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { context, ledger, llm, usage, fmtTokens } from '../data/panels'
import { catalog } from '../data/store'
import { streamState } from '../data/stream'
import { turnSkills } from '../data/wire'

/* The prototype filled this panel with literals: four fixed context segments, a
   fixed agent line, six fixed skill names and three invented tool rows. Everything
   below is derived from state that actually exists. */

/* Window occupancy uses the old shell's model: the latest request's input plus
   output against the model's context limit. The gateway reports both in every
   `message.usage`, so this is measured, not guessed. */
/* Window occupancy: the latest call's prompt plus its own output, not the session
   totals — those run to hundreds of millions on a long conversation. */
const usedTokens = computed(() => (usage.contextTokens || 0) + (usage.contextOut || 0))
/* The window size is only sent with a usage payload. When it is missing, derive
   it from the reported percentage — the same fallback the island uses — and only
   then fall back to the old shell's nominal million. */
const windowSize = computed(() => {
  if (usage.contextLimit) return usage.contextLimit
  if (usage.contextPct > 0 && usage.contextTokens > 0) {
    return Math.round(usage.contextTokens / (usage.contextPct / 100))
  }
  return 1_000_000
})

/* The gateway estimates four parts locally on every call: the agent prompt, the
   preloaded skill block, the tool schemas and the conversation itself. The pane
   scales all four against the provider's measured prompt_tokens, so their sum
   equals what was actually billed. */
const split = computed(() => {
  const limit = windowSize.value
  if (!limit) return null
  const used = Math.min(usedTokens.value, limit)
  /* The breakdown covers the input side only; the completion is part of what
     occupies the window, so it stays inside the conversation share. */
  const prompt = Math.min(usage.contextTokens || used, used)

  let base = usage.contextBase
  let skills = usage.contextSkills
  let tool = usage.contextTools
  const conv = usage.contextConv
  /* An older gateway reports no breakdown. Show the request as conversation
     rather than inventing a split. */
  if (!base && !skills && !tool && !conv) {
    return { base: 0, skills: 0, conv: used, tool: 0, free: Math.max(0, limit - used), limit }
  }
  /* All four parts are local tokenizer estimates while `prompt` is the
     provider's measured count. Scaling them by one factor keeps their sum equal
     to that measurement, so the difference between tokenizers is shared instead
     of being charged to the conversation alone. The ratio stays near 1 in
     practice; a wild one means the estimate missed something (an attachment, an
     image) and subtraction is the safer answer. */
  const localTotal = base + skills + tool + conv
  /* `conv` is only present when the gateway sends a message estimate. Without
     it the four-part split would scale the three prompt parts up to cover a
     conversation that is not in their sum, so the old subtraction stays. */
  if (conv > 0 && localTotal > 0 && prompt > 0) {
    const scale = prompt / localTotal
    if (scale >= 0.2 && scale <= 3) {
      base = Math.round(base * scale)
      skills = Math.round(skills * scale)
      tool = Math.round(tool * scale)
      /* The completion is not part of the prompt estimate, so it is added back
         to the conversation share rather than scaled with it. */
      const convInput = Math.round(conv * scale)
      const convTotal = Math.max(0, Math.min(convInput + Math.max(0, used - prompt), used - base - skills - tool))
      return { base, skills, conv: convTotal, tool, free: Math.max(0, limit - used), limit }
    }
  }
  /* Fallback: no message estimate, or a ratio too far off to trust. */
  const known = base + skills + tool
  if (known > prompt && known > 0) {
    const scale = prompt / known
    base = Math.round(base * scale)
    skills = Math.round(skills * scale)
    tool = Math.round(tool * scale)
  }
  const convFallback = Math.max(0, used - base - skills - tool)
  return { base, skills, conv: convFallback, tool, free: Math.max(0, limit - used), limit }
})

const segments = computed(() => {
  const s = split.value
  if (!s) {
    return [{ cls: 'seg-free', pct: 100, color: 'var(--surface-3)', label: 'no window reported', value: '—' }]
  }
  const pct = (n: number) => (n > 0 ? Math.max(1, Math.round((n / s.limit) * 100)) : 0)
  return [
    { cls: 'seg-sys', pct: pct(s.base), color: 'var(--teal)', label: 'system prompt', value: `${s.base.toLocaleString()} · ${pct(s.base)}%` },
    { cls: 'seg-skill', pct: pct(s.skills), color: 'var(--ok)', label: 'preloaded skills', value: `${s.skills.toLocaleString()} · ${pct(s.skills)}%` },
    { cls: 'seg-hist', pct: pct(s.conv), color: 'var(--gold)', label: 'conversation', value: `${s.conv.toLocaleString()} · ${pct(s.conv)}%` },
    { cls: 'seg-tool', pct: pct(s.tool), color: 'var(--accent)', label: 'tool schemas', value: `${s.tool.toLocaleString()} · ${pct(s.tool)}%` },
    { cls: 'seg-free', pct: pct(s.free), color: 'var(--surface-3)', label: 'free', value: `${s.free.toLocaleString()} · ${pct(s.free)}%` }
  ]
})

const agentName = computed(() => llm.agents.find(a => a.current)?.name || context.agentName || 'agent')

const toolCount = computed(() => catalog.toolGroups.reduce((n, g) => n + g.items.length, 0))
const skillCount = computed(() => catalog.skillGroups.reduce((n, g) => n + g.items.length, 0))
const agentSub = computed(() => {
  const model = streamState.model || llm.model || context.agentSub
  return `${model} · ${toolCount.value} tools · ${skillCount.value} skills`
})

/* Skills come from the catalogue the gateway returned in `session.info`; before
   that lands there is nothing honest to show, so the row stays empty rather than
   listing the prototype's names. */
/* Loaded means this turn: the agent's preload list plus whatever it pulled in
   with `load_skill` since the turn started. The second half resets next turn,
   and the on-disk catalogue is a different list (the tools sheet browses it). */
const preloadedSkills = computed(() => catalog.agentSkills)
const turnLoaded = computed(() => turnSkills.names)
const skills = computed(() => {
  const merged = new Set<string>([...preloadedSkills.value, ...turnLoaded.value])
  return [...merged].sort((a, b) => a.localeCompare(b))
})
const isTurnLoaded = (name: string) => turnLoaded.value.includes(name)

/* `skills loaded` answers what this conversation pulled in, not what is
   installed, so a skill sitting in the user folder but never loaded is absent
   here. The hint under it answers the other question. The gateway omits an empty
   group, so no `User` entry means that folder is empty. */
const hasUserSkills = computed(() => catalog.skillGroups.some(g => g.name === 'User'))

/* Tool rows for the current turn are read off the transcript: the newest agent
   row carries its tool blocks in order. Before any block exists the list stays
   empty — the prototype's three sample rows are gone. */
const toolsExpanded = ref(false)

/* The gateway reports what each tool's output cost this turn, which is the part
   worth seeing in a coding session: the file read that filled the window, not
   just the fact that a tool ran. Rows arrive largest first. */
const toolRows = computed(() => {
  if (usage.toolResults.length) {
    return usage.toolResults.map(r => ({
      ico: 'mod',
      mark: '→',
      text: r.calls > 1 ? `${r.name} ×${r.calls}` : r.name,
      time: fmtTokens(r.tokens)
    }))
  }
  /* Older gateway: names only, read off the transcript's tool blocks. */
  for (let i = streamState.items.length - 1; i >= 0; i--) {
    const item = streamState.items[i]
    if (!item.blocks) continue
    return item.blocks
      .filter(b => b.kind === 'tool')
      .slice(-6)
      .map(b => ({
        ico: 'mod',
        mark: '→',
        text: String((b as { call?: { name?: string } }).call?.name ?? 'tool'),
        time: ''
      }))
  }
  return []
})

/* Detail, not the main readout: five rows stay visible and the rest fold away so
   a long turn cannot push the sections below off screen. */
const visibleTools = computed(() => (toolsExpanded.value ? toolRows.value : toolRows.value.slice(0, 5)))
const hiddenToolCount = computed(() => Math.max(0, toolRows.value.length - 5))

/* A new turn starts folded again. */
watch(toolRows, () => { toolsExpanded.value = false })

const ledgerSub = computed(() => {
  const by: Record<string, number> = {}
  for (const e of ledger.entries) by[e.kind] = (by[e.kind] ?? 0) + 1
  const parts = Object.entries(by).map(([kind, n]) => `${n} ${kind}`)
  return parts.length ? parts.join(' · ') : 'no entries yet'
})
</script>
