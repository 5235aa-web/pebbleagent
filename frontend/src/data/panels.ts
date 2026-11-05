/* Dock pane state.

   No prototype numbers: every field starts at zero, so an unmeasured window
   reads as nothing rather than as a plausible-looking bill. The RPCs fill the
   same fields in. */

import { reactive } from 'vue'

/** The price table is kept in one currency (CNY, see model_pricing.json). The
    display switch never converts what is stored — it only changes how an amount
    is read — so a wrong rate can never corrupt a bill. It lives here so a stale
    figure has exactly one place to fix. */
export const CNY_PER_USD = 7.2

/** True for the OpenCode family (`opencode`, `OpenCode Go`, `opencode-go`).
    The request-shaping block below the divider is specific to that gateway,
    so every other provider card hides it instead of showing dead controls. */
export function isOpenCodeProvider(name: string): boolean {
  const normalized = String(name || '').toLowerCase().replace(/[\s_-]+/g, '')
  return normalized === 'opencode' || normalized === 'opencodego'
}

/** A provider can serve chat only with a base URL and at least one model.
    The API key is a separate warning: local endpoints often accept any key. */
export function isProviderReady(provider: { baseUrl?: string; models?: string[] } | null | undefined): boolean {
  return Boolean(provider && String(provider.baseUrl || '').trim() && (provider.models || []).length)
}

function storedDisplayCurrency(): 'cny' | 'usd' {
  try {
    if (typeof window === 'undefined') return 'cny'
    return window.localStorage.getItem('pebble.displayCurrency') === 'usd' ? 'usd' : 'cny'
  } catch { return 'cny' }
}

export interface BillRow {
  model: string
  tokensIn: number
  tokensOut: number
  cost: number
  tint: string
}

export const usage = reactive({
  range: 'session' as 'session' | 'today' | 'week' | 'month',
  /* No prototype placeholder: an unmeasured window reads as zero, so the ring can
     never display a plausible-looking 31% that no payload produced. */
  contextPct: 0,
  /* Window occupancy of the latest request, kept apart from `tokensIn`: that one
     is the session total from `usage.stats`, while these two describe the single
     request the agent is working inside right now. */
  contextTokens: 0,
  contextLimit: 0,
  /* Output tokens of the latest call, kept out of `tokensOut` for the same reason. */
  contextOut: 0,
  /* Real prompt parts measured by the gateway: the agent prompt (experience,
     project path and user note included), the preloaded SKILL.md block, and the
     tool schemas. The conversation share is what the provider's prompt_tokens
     leaves after these three, so the split always adds up to the real total
     instead of being a scaled guess. */
  contextBase: 0,
  contextSkills: 0,
  contextTools: 0,
  costToday: 0,
  /* Rolling windows over the same table, filled by `refreshPanes`. */
  cost7d: 0,
  cost30d: 0,
  currency: 'cny',
  /* What the reader wants to see, not what the gateway stores. */
  displayCurrency: storedDisplayCurrency(),
  tokensIn: 0,
  tokensOut: 0,
  cacheHit: 0,
  calls: 0,
  sessionCost: 0,
  history: [] as BillRow[],
  /* Per-model totals straight from `usage.stats.models`, used by the context
     pane's agent snapshot instead of the prototype's fixed counters. */
  models: [] as Array<{ model: string; calls: number; cost: number; tokensIn: number; tokensOut: number }>
})

export interface DiffLine {
  prefix: string
  text: string
}

export interface DiffHunk {
  header: string
  lines: DiffLine[]
}

export interface FileChange {
  path: string
  dir: string
  base: string
  status: string
  added: number
  removed: number
  versions: number
  open: boolean
  hunks: DiffHunk[]
}

export interface WhitelistPath {
  path: string
  mode: string
}

/* Empty until `session.file_changes` answers. The prototype seeded four files
   from another project (`components/InputArea.vue`, `design-preview/…`) with
   invented +/- counts, and a session that had edited nothing kept showing them;
   this is the same fake-data problem the ledger and MCP panels carried.
   `state` separates "not asked yet" from "asked, and there is nothing".
   `paths` is filled by `path_whitelist.read`; the prototype shipped two paths
   from the author's machine, one pointing at this very repository. */
export const files = reactive({
  changes: [] as FileChange[],
  totalFiles: 0,
  totalVersions: 0,
  totalAdded: 0,
  totalRemoved: 0,
  /* 'idle' until the first answer. A failed read is not an empty session, so
     the pane says which one it is instead of leaving "loading…" up forever. */
  state: 'idle' as 'idle' | 'loading' | 'ok' | 'failed',
  paths: [] as WhitelistPath[],
  /* Entries the gateway will not accept on save (a drive root, a relative
     path). The file can carry them from an older build or a hand edit, and
     while they are in it every save is refused — which made the pane unable to
     add, edit or remove anything. Shown separately so they can be deleted. */
  rejected: [] as Array<{ path: string; reason: string }>
})

export interface McpServer {
  name: string
  transport: string
  logo: string
  cmd: string
  tools: number
  on: boolean
  open: boolean
  state: string
  chips: string[]
  more: number
  /* Every tool the server exposes. `chips` is only the first four, which is
     enough for a collapsed row but hides what an enabled server actually adds. */
  toolNames?: string[]
  /* Whether its command resolves to something runnable. A server configured on
     another machine (npx with no Node, a REPLACE_WITH_ path) cannot start, and
     the panel says which prerequisite is missing instead of showing a token. */
  ready: boolean
  missing: string[]
  /* Declared in mcp_install.json, so the row can offer Install instead of only
     reporting what is missing. */
  installable: boolean
  /* True only when the server's env carries a credential variable. Drawing the
     token field unconditionally put "paste token" on stdio servers that never
     read one. */
  requiresToken: boolean
  /* The env variable the credential belongs in (GITHUB_PERSONAL_ACCESS_TOKEN
     and friends). Writing a fixed MCP_TOKEN handed github a key it never reads. */
  tokenEnv: string
  token: string
}

/* A server declared in mcp_install.json but absent from .mcp.json. */
export interface McpAvailable {
  name: string
  desc: string
  missing: string[]
}

export const mcp = reactive({
  /* Empty until `mcp.list` answers. The prototype seeded github / filesystem /
     postgres, which is the same fake-data problem the session switcher had: an
     account with none of them kept showing three. */
  servers: [] as McpServer[],
  running: 0,
  idle: 0,
  tools: 0,
  /* Approvals the reader still owes. A project-level file is trusted as a whole,
     one fingerprint per file; a user-level entry is trusted one server at a
     time, which is what lets the agent write one and have it wait here instead
     of quietly starting a process. */
  pendingProject: null as { path: string; servers: string[] } | null,
  pendingUser: [] as Array<{ name: string; target: string }>,
  /* Declared in mcp_install.json but not yet in .mcp.json: the install row
     points here. This replaces a hardcoded `postgres` placeholder that never
     matched anything the backend could actually install. */
  available: [] as McpAvailable[]
})

export interface LedgerEntry {
  id: string
  text: string
  scope: string
  kind: string
  confidence: number
  age: string
  rationale?: string
  appliesTo?: string[]
  status?: string
}

/* Empty until `refreshPanes` answers with `memory.list`. The prototype seeded
   five demo entries and a total of 14, and an account with no memories kept
   showing them — the same fake-data problem the session switcher had. */
export const ledger = reactive({
  entries: [] as LedgerEntry[],
  total: 0,
  /* How the last read went, so the panel can say loading / failed / empty
     instead of rendering all three as an empty list. */
  state: 'idle' as 'idle' | 'loading' | 'ok' | 'failed'
})

export interface CtxSegment {
  cls: string
  pct: number
  color: string
  label: string
  value: string
}

export const context = reactive({
  segments: [
    { cls: 'seg-sys', pct: 14, color: 'var(--teal)', label: 'system prompt', value: '8,720 · 4.4%' },
    { cls: 'seg-hist', pct: 38, color: 'var(--gold)', label: 'conversation', value: '23,684 · 11.8%' },
    { cls: 'seg-tool', pct: 17, color: 'var(--accent)', label: 'tool results', value: '10,592 · 5.3%' },
    { cls: 'seg-free', pct: 31, color: 'var(--surface-3)', label: 'free', value: '137,860 · 68.9%' }
  ] as CtxSegment[],
  agentName: 'code_agent',
  agentSub: 'claude-sonnet-4.5 · 45 tools · 12 skills',
  skills: ['ckb', 'design', 'diagnose', 'tdd', 'read-the-damn-docs', 'quick-recap'],
  tools: [
    { ico: 'mod', mark: '\u2192', text: 'grep · 6 matches', time: '0.08s' },
    { ico: 'mod', mark: '\u2192', text: 'edit_file · InputArea.vue', time: '2.4s' },
    { ico: 'del', mark: '\u00d7', text: 'execute_shell · TS2339', time: '6.1s' }
  ],
  ledgerTotal: 0,
  ledgerSub: ''
})

export interface ClarifyOption {
  label: string
  detail: string
}

/** One question's answer. Presets and free-form text stay apart: a single
    joined string left option labels and typed text indistinguishable. */
export interface ClarifyAnswer {
  picked: string[]
  custom: string
}

export interface ClarifyQuestion {
  title: string
  prompt: string
  options: ClarifyOption[]
  multiple: boolean
}

export interface PairingState {
  supported: boolean
  enabled: boolean
  port: number
  host: string
  fingerprint: string
  certExpiresAt: string
  code: string
  uri: string
  error: string
}

export const pairing = reactive<PairingState>({
  supported: true,
  enabled: false,
  port: 0,
  host: '',
  fingerprint: '',
  certExpiresAt: '',
  code: '',
  uri: '',
  error: ''
})

export interface SearchHit {
  sessionId: string
  messageId: number
  role: string
  text: string
  createdAt: string
}

export interface ImageState {
  path: string
  name: string
  dataUrl: string
  meta: string
}

export const image = reactive<ImageState>({
  path: '',
  name: '',
  dataUrl: '',
  meta: ''
})

export const search = reactive({
  open: false,
  query: '',
  hits: [] as SearchHit[],
  total: 0
})

export interface QuickNote {
  time: string
  text: string
}

/** A private note. The id is what the editor and the delete button address, so
    an entry keeps its identity while others around it are added or removed. */
export interface NotebookEntry {
  id: string
  time: string
  text: string
}

export const notes = reactive({
  quick: [] as QuickNote[],
  notebook: [] as NotebookEntry[],
  usernote: '',
  usernoteLoaded: false
})

export interface LlmAgent {
  name: string
  desc: string
  initial: string
  tint: string
  current: boolean
}

export interface LlmProvider {
  name: string
  slug: string
  models: string[]
  authenticated: boolean
  current: boolean
  ready: boolean
  apiKey: string
  baseUrl: string
  temperature: number
  topP: number
  reasoningEffort: string
  thinkingEnabled: boolean
  extraHeaders: string
  sessionHeader: string
  reasoningMode: string
  maxEffort: string
  /** Local-only: a field changed after the last successful save. Refresh keeps
      the object instead of replacing it, so a reconnect cannot discard a
      half-typed key or base url. */
  dirty: boolean
}

export const llm = reactive({
  agents: [] as LlmAgent[],
  providers: [] as LlmProvider[],
  model: '',
  provider: '',
  currency: 'CNY',
  pricing: {} as Record<string, { input?: number; cache_hit?: number; output?: number; tier_threshold?: number }>,
  temperature: 1,
  topP: 1,
  reasoningEffort: 'high',
  thinkingEnabled: true,
  reasoningMode: 'thinking',
  /** Request shaping for OpenCode Go style gateways: whether a `max` effort is
      downgraded to `high` because the endpoint rejects it. */
  maxEffort: 'downgrade',
  sessionHeader: '',
  extraHeaders: '{}',
  /** False until model.options answers, so the pane can keep its prototype look. */
  loaded: false
})

export function fmtTokens(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return String(n)
}

export function fmtCost(n: number, currency = 'cny'): string {
  const sign = currency.toLowerCase() === 'cny' ? '\u00a5' : '$'
  return sign + n.toFixed(n < 0.01 ? 3 : 2)
}

/** Money as the reader wants to read it. `bookCurrency` is what the gateway
    recorded, which is CNY for every row today; the switch converts for display
    only, so the stored table and the stored costs never move. */
export function fmtMoney(n: number, bookCurrency = usage.currency): string {
  const target = usage.displayCurrency
  const from = (bookCurrency || 'cny').toLowerCase()
  if (from === target) return fmtCost(n, target)
  const converted =
    from === 'cny' && target === 'usd' ? n / CNY_PER_USD
      : from === 'usd' && target === 'cny' ? n * CNY_PER_USD : n
  return fmtCost(converted, target)
}

export function setDisplayCurrency(next: 'cny' | 'usd'): void {
  usage.displayCurrency = next
  try { window.localStorage.setItem('pebble.displayCurrency', next) } catch { /* private mode */ }
}
