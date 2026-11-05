<template>
        <div class="dr-pane on" data-pane="files">
          <div class="section-h">snapshots <span class="sh-note">{{ snapshotNote }}</span></div>
          <div class="tl">
            <div
              v-for="(s, i) in visibleSnapshots"
              :key="s.id"
              class="tl-row"
              :class="{ now: i === 0 && !s.reverted, base: i === visibleSnapshots.length - 1 && !hiddenSnapshots }"
            >
              <span class="tl-dot"></span>
              <div class="tl-main">
                <div class="tl-t">
                  <span v-if="(s.count ?? 1) > 1" class="tl-mult">×{{ s.count }}</span>
                  before {{ s.tool || 'edit' }}
                </div>
                <div class="tl-m">{{ s.files }} file{{ s.files === 1 ? '' : 's' }} · {{ s.reverted ? 'reverted' : 'revertable' }}</div>
              </div>
              <span class="tl-time">{{ clock(s.createdAt) }}</span>
              <button v-if="!s.reverted" class="tl-rev tip" data-tip="Revert to this snapshot" @click="revertSnapshot(s)">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="1 4 1 10 7 10"/><path d="M3.5 15a9 9 0 1 0 2.1-9.4L1 10"/></svg>
              </button>
              <span v-else class="tl-rev off"></span>
            </div>
            <button v-if="hiddenSnapshots && !showAllSnapshots" class="tl-more" @click="showAllSnapshots = true">
              show {{ hiddenSnapshots }} earlier
            </button>
            <button v-else-if="showAllSnapshots && hiddenSnapshots" class="tl-more" @click="showAllSnapshots = false">
              collapse
            </button>
            <div v-if="!snapshots.length" class="tl-row base">
              <span class="tl-dot"></span>
              <div class="tl-main">
                <div class="tl-t">session start</div>
                <div class="tl-m">{{ snapshotLoaded ? 'no snapshots yet' : 'loading…' }}</div>
              </div>
              <span class="tl-time">—</span>
              <span class="tl-rev off"></span>
            </div>
          </div>




          <div class="section-h">files changed <span class="sh-note">{{ files.totalFiles }} files · {{ files.totalVersions }} versions</span>
            <button class="sh-act" id="chgAccept" @click="acceptAll">Accept all</button>
          </div>
          <div class="chg-list" id="chgList">
            <div class="tl-row base" v-if="!files.changes.length">
              <span class="tl-dot"></span>
              <div class="tl-main">
                <div class="tl-t">no changes</div>
                <div class="tl-m">{{ files.state === 'ok' ? 'no file edited in this session' : files.state === 'failed' ? 'could not read the change list' : 'loading…' }}</div>
              </div>
              <span class="tl-time">—</span>
              <span class="tl-rev off"></span>
            </div>
            <div class="chg" v-for="(f, i) in files.changes" :key="i" :class="{ open: f.open }">
              <button class="chg-row" @click="toggleDiff(f)">
                <span class="chg-st" :class="f.status">{{ stLetter(f.status) }}</span>
                <span class="chg-path"><span class="chg-dir">{{ f.dir }}</span>{{ f.base }}</span>
                <span class="chg-ver">{{ f.versions }}v</span>
                <span class="chg-num"><b class="up" v-if="f.added">+{{ f.added.toLocaleString() }}</b><b class="down" v-if="f.removed">-{{ f.removed.toLocaleString() }}</b></span>
                <span class="chg-rev tip" data-tip="Revert" @click.stop="revertOne(f)">&#8634;</span>
              </button>
              <div class="chg-diff" v-if="f.open && f.hunks.length">
                <template v-for="(h, hi) in f.hunks" :key="hi">
                  <div class="cd-hunk">{{ h.header }}</div>
                  <div class="cd-line" :class="l.prefix === '+' ? 'add' : 'del'" v-for="(l, li) in h.lines" :key="li"><i>{{ l.prefix }}</i>{{ l.text }}</div>
                </template>
              </div>
            </div>
          </div>
          <div class="section-h">whitelisted paths
            <button class="sh-act" data-sheet="sheet-paths">Edit</button>
          </div>
          <div class="section-sub">up to 5 paths the LLM can access</div>
          <div class="snap" v-for="(p, i) in files.paths" :key="i">
            <span class="snap-ico">
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
            </span>
            <div class="snap-main">
              <div class="snap-title">{{ p.path }}</div>
              <div class="snap-sub">{{ p.mode }}</div>
            </div>
          </div>
        </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { files, type DiffHunk, type FileChange } from '../data/panels'
import { acceptFileChanges, fileDiff, refreshFileChanges, revertFileChange } from '../data/wire'
import { gateway } from '../data/gateway'
import { sessionState } from '../data/store'
import { clockOf } from '../data/render'

/* ---- snapshots ----

   The gateway keeps one snapshot record per file a tool touched, so grouping
   them by `snapshot_id` turns them back into the timeline entry the pane shows.
   A record is written by every write / edit / delete / move, which is why the
   entry is named after the tool that caused it. */
interface SnapshotRow {
  id: string
  tool: string
  messageId: number
  files: number
  paths: string[]
  createdAt: number
  reverted: boolean
  /** How many raw rows this entry stands for (only set after grouping). */
  count?: number
}

const snapshots = ref<SnapshotRow[]>([])
const snapshotLoaded = ref(false)
const showAllSnapshots = ref(false)
const SNAPSHOT_PREVIEW = 6

/** Twelve edits inside the same minute are one procedure, not twelve entries.
    Neighbouring rows from the same tool that land within a minute of each other
    collapse into one entry, which is what turns a 29-row wall into a list you
    can read. Reverting a group reverts every path it collected. */
function groupSnapshots(rows: SnapshotRow[]): SnapshotRow[] {
  const out: SnapshotRow[] = []
  for (const row of rows) {
    const last = out[out.length - 1]
    const close =
      last &&
      last.tool === row.tool &&
      !last.reverted &&
      Math.abs((last.createdAt || 0) - (row.createdAt || 0)) <= 60
    if (close) {
      last.count = (last.count ?? 1) + 1
      last.files = Math.max(last.files, row.files)
      last.paths = Array.from(new Set([...last.paths, ...row.paths]))
      last.id = row.id + '+' + last.id
      continue
    }
    out.push({ ...row, paths: [...row.paths], count: 1 })
  }
  return out
}

const groupedSnapshots = computed(() => groupSnapshots(snapshots.value))
const visibleSnapshots = computed(() =>
  showAllSnapshots.value ? groupedSnapshots.value : groupedSnapshots.value.slice(0, SNAPSHOT_PREVIEW)
)
const hiddenSnapshots = computed(() =>
  Math.max(0, groupedSnapshots.value.length - SNAPSHOT_PREVIEW)
)
const snapshotNote = computed(() => {
  if (!snapshotLoaded.value) return 'session timeline'
  const groups = groupedSnapshots.value.length
  const total = snapshots.value.length
  return groups === total ? `${total} snapshots` : `${groups} of ${total} snapshots`
})

async function loadSnapshotList(): Promise<void> {
  if (!gateway.connected || !sessionState.activeId) return
  try {
    const res = await gateway.request<{ snapshots?: SnapshotRow[] }>('session.snapshots', {
      session_id: sessionState.activeId
    })
    snapshots.value = Array.isArray(res?.snapshots) ? res.snapshots : []
  } catch {
    snapshots.value = []
  }
  snapshotLoaded.value = true
}

function clock(ts: number): string {
  return clockOf(ts) || '—'
}

/** Reverting a snapshot means reverting every file it captured, which is what
    the per-file revert call already does. */
async function revertSnapshot(s: SnapshotRow): Promise<void> {
  for (const path of s.paths) await revertFileChange(path)
  await loadSnapshotList()
}

onMounted(() => {
  void loadSnapshotList()
  /* The change list is normally pushed by session load and by `file.change`,
     but a pane that mounts before any of those ran sat on "loading…" forever.
     Asking once here also turns a missing gateway into a stated failure. */
  void refreshFileChanges()
})

watch(
  () => sessionState.activeId,
  () => {
    void loadSnapshotList()
  }
)

function stLetter(status: string): string {
  return status === 'add' ? 'A' : status === 'del' ? 'D' : 'M'
}

/** The diff is fetched on first open only: a change list can hold dozens of
    files and the gateway rebuilds each diff from the snapshot records. */
async function toggleDiff(f: FileChange): Promise<void> {
  if (f.hunks.length) {
    f.open = !f.open
    return
  }
  const raw = await fileDiff(f.path)
  f.hunks = raw.map<DiffHunk>(h => {
    const lines = Array.isArray(h.lines) ? (h.lines as Array<Record<string, unknown>>) : []
    return {
      header: String(h.header ?? h.hunk ?? ''),
      lines: lines.map(l => ({
        prefix: String(l.prefix ?? l.sign ?? ' '),
        text: String(l.text ?? l.content ?? '')
      }))
    }
  })
  f.open = true
}

async function revertOne(f: FileChange): Promise<void> {
  await revertFileChange(f.path)
}

async function acceptAll(): Promise<void> {
  await acceptFileChanges()
}
</script>
