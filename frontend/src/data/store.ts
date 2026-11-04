import { reactive } from 'vue'

/* Front-end state for the reconstructed surface.
   Everything the prototype hard-coded in markup lives here, so the gateway can
   later feed the same shapes without the components changing. */

export type SessionGroup =
  | 'Pinned'
  | 'Today'
  | 'Yesterday'
  | 'This week'
  | 'Last week'
  | 'This month'
  | 'Last month'
  | 'Earlier'
  | 'Last year'

const DAY_MS = 86400000

/* Buckets a session by the timestamp of its last message. Weeks start on Monday
   and the checks run newest-first, so a session inside the current week is not
   also counted as "this month". Anything before January of this year lands in
   "Last year". */
export function bucketFor(ts: number, now = new Date()): SessionGroup {
  if (!Number.isFinite(ts) || ts <= 0) return 'Earlier'
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const startOfWeek = startOfToday - ((now.getDay() + 6) % 7) * DAY_MS
  const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1).getTime()
  if (ts >= startOfToday) return 'Today'
  if (ts >= startOfToday - DAY_MS) return 'Yesterday'
  if (ts >= startOfWeek) return 'This week'
  if (ts >= startOfWeek - 7 * DAY_MS) return 'Last week'
  if (ts >= startOfMonth) return 'This month'
  if (ts >= new Date(now.getFullYear(), now.getMonth() - 1, 1).getTime()) return 'Last month'
  if (ts >= new Date(now.getFullYear(), 0, 1).getTime()) return 'Earlier'
  return 'Last year'
}

export interface Session {
  id: string
  index: string | null
  title: string
  msgs: number
  time: string
  group: SessionGroup
  pinned: boolean
  /* Timestamp of the last message; the group is derived from it, and un-pinning
     needs it to put the row back where it belongs. */
  ts?: number
}

export type StepState = 'done' | 'now' | 'queued'

export interface PlanStep {
  id: string
  task: string
  note: string
  state: StepState
}

export type RiskLevel = 'critical' | 'high' | 'low'
export type RequestIcon = 'command' | 'write' | 'read'
export type Verdict = 'allowed' | 'denied' | null

export interface PermissionRequest {
  id: string
  level: RiskLevel
  icon: RequestIcon
  kind: string
  target: string
  targetKey: string
  reason: string
  reqId: string
  duration: number
  settled: boolean
  verdict: Verdict
  leaving: boolean
}

/* Empty until `bootstrap` answers with the rows the gateway holds for this
   project. The prototype used to seed five demo conversations here, and a
   project with no sessions kept showing them: they were clickable, they
   answered `session.list` from nobody, and the switcher looked populated on a
   fresh install. An empty list is a real state; fake rows are not. */
export const sessions = reactive<Session[]>([])

export const sessionState = reactive({
  activeId: '',
  filter: '',
  /* How the switcher's last read went. Without it an empty panel cannot say
     whether it is still loading, failed to read, or has nothing to show. */
  listState: 'idle' as 'idle' | 'loading' | 'ok' | 'failed',
  /* Status-bar counters. Both come from session.resume: `info.tools` and
     `info.skills` carry what the gateway loaded, and the tool-call total is
     counted from the restored transcript. */
  toolCount: 0,
  skillCount: 0,
  toolCalls: 0,
  /* Every row the gateway holds for the session (user, assistant, tool and
     compaction summaries), which is what the header reports. */
  messageCount: 0,
  projectPath: ''
})

/* The project registry the gateway owns. `active` is the directory every tool
   call is scoped to, so it sits beside the session state the switcher reads
   rather than in a panel of its own. */
export const projects = reactive({
  list: [] as string[],
  active: '',
  /** False until project.list answers, so the switcher can avoid flashing an
      empty registry over a real one. */
  loaded: false,
  /** True when projects.json exists but could not be read. The panel then says
      the registry is unreadable instead of drawing an empty one. */
  corrupt: false,
  /** Where the preserved copy of an unreadable registry was written. */
  backup: ''
})

export interface CatalogGroup {
  name: string
  items: string[]
}

/* The tool and skill catalogue from session.info. Grouped the way the gateway
   sends it (tools under "Agent", skills by folder), so the sheet can render the
   same headings the prototype shows. */
export const catalog = reactive({
  toolGroups: [] as CatalogGroup[],
  skillGroups: [] as CatalogGroup[],
  /* Skills the active agent preloads into its prompt (the yaml `skills:` list).
     `skillGroups` answers a different question: every skill directory on disk.
     The context panel shows this list plus whatever the turn loads itself. */
  agentSkills: [] as string[]
})

export function setCatalog(tools: unknown, skills: unknown): void {
  const toGroups = (raw: unknown): CatalogGroup[] => {
    if (!raw || typeof raw !== 'object') return []
    return Object.entries(raw as Record<string, unknown>)
      .map(([name, items]) => ({
        name,
        items: Array.isArray(items) ? (items as unknown[]).map(String) : []
      }))
      .filter(g => g.items.length > 0)
  }
  catalog.toolGroups = toGroups(tools)
  catalog.skillGroups = toGroups(skills)
}

/* The plan slip is filled by the gateway from `todos.update`; nothing is seeded.
   Opening with sample steps put a screen of fiction in front of the reader for
   as long as the sidecar took to boot. */
export const plan = reactive({
  open: false,
  title: '',
  sub: '',
  token: '',
  steps: [] as PlanStep[]
})

/* The permission queue is filled by the gateway when a turn asks for approval;
   nothing is seeded. */
export const permissions = reactive({
  open: false,
  queue: [] as PermissionRequest[]
})

/* ---- derived helpers ---- */

export function planProgress() {
  const done = plan.steps.filter(s => s.state === 'done').length
  return { done, total: plan.steps.length }
}

export function activeSession() {
  return sessions.find(s => s.id === sessionState.activeId) || null
}

export function selectSession(id: string) {
  if (!sessions.some(s => s.id === id)) return null
  sessionState.activeId = id
  return activeSession()
}

export function togglePin(id: string) {
  const s = sessions.find(x => x.id === id)
  if (!s) return false
  s.pinned = !s.pinned
  // Un-pinning returns the row to its time bucket instead of dumping it in Today.
  s.group = s.pinned ? 'Pinned' : bucketFor(s.ts ?? 0)
  return s.pinned
}

export function renameSession(id: string, title: string) {
  const s = sessions.find(x => x.id === id)
  if (!s) return
  s.title = title
}

export function removeSession(id: string) {
  const i = sessions.findIndex(s => s.id === id)
  if (i < 0) return null
  const [gone] = sessions.splice(i, 1)
  if (sessionState.activeId === id && sessions.length) sessionState.activeId = sessions[0].id
  return gone
}

export function findPermission(id: string) {
  return permissions.queue.find(r => r.id === id) || null
}

export function settlePermission(id: string, allow: boolean) {
  const r = findPermission(id)
  if (!r || r.settled) return null
  r.settled = true
  r.verdict = allow ? 'allowed' : 'denied'
  return r
}

export function dropPermission(id: string) {
  const i = permissions.queue.findIndex(r => r.id === id)
  if (i < 0) return null
  const [gone] = permissions.queue.splice(i, 1)
  return gone
}

export function pendingPermissions() {
  return permissions.queue.filter(r => !r.settled)
}

export function riskSummary() {
  const out: Record<RiskLevel, number> = { critical: 0, high: 0, low: 0 }
  for (const r of pendingPermissions()) out[r.level] += 1
  return out
}
