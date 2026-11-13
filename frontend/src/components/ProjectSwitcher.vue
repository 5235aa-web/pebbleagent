<template>
    <button class="project-chip tip tip-left" data-tip="Switch project" id="projectChip">
      <span class="pc-icon">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
      </span>
      <span class="pc-name">{{ activeName }}</span>
      <svg class="pc-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
    </button>

    <div class="proj-pop" id="projPop" :style="pvStyle">
      <div class="pp-head">
        <span class="pp-folder">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        </span>
        <div class="pp-cur">
          <div class="pp-name">{{ activeName }}</div>
          <div class="pp-path">{{ projects.active || 'no project selected' }}</div>
        </div>
        <button class="pp-switch" id="ppSwitch" @click="onRefresh">Refresh
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1"/><polyline points="20.5 3.5 20.5 9.5 14.5 9.5"/></svg>
        </button>
      </div>

      <div v-if="projects.corrupt" class="pp-err">
        projects.json is unreadable; a copy was kept at {{ projects.backup || 'projects.json.corrupt' }}.
        the list below is what could be recovered, not the whole registry.
      </div>

      <div class="pp-list" id="ppList">
        <button
          v-for="p in projects.list"
          :key="p"
          class="pp-item"
          :class="{ on: p === projects.active }"
          @click="onPick(p)"
        >
          <span class="pp-glyph">&#9656;</span>
          <span class="pp-txt"><b>{{ baseName(p) }}</b><small>{{ p }}</small></span>
          <span v-if="p === projects.active" class="pp-mark">&#10003;</span>
          <span v-else class="pp-x" @click.stop="onRemove(p)">&#215;</span>
        </button>
        <div v-if="projects.loaded && !projects.corrupt && !projects.list.length" class="pp-empty">no projects registered yet</div>
      </div>

      <button class="pp-adopt" id="ppAdopt" @click="onAdopt">
        <span>adopt <b>{{ unassigned }}</b> unassigned sessions → current root</span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="4" y1="12" x2="19" y2="12"/><polyline points="13 6 19 12 13 18"/></svg>
      </button>

      <div class="pp-add">
        <input
          id="ppAddInput"
          v-model="addPath"
          placeholder="add project path..."
          @keydown.enter.prevent="onAdd"
        />
        <button id="ppAddBtn" aria-label="Add project" @click="onAdd">+</button>
      </div>
      <div v-if="addError" class="pp-err">{{ addError }}</div>

      <div class="pp-body">
        <div class="pp-left">
          <div class="pp-tree-h">files <span>{{ treeHint }}</span>
            <button class="pp-refresh" @click="reloadTree">refresh</button>
          </div>
          <div class="pp-tree">
            <div v-if="treeLoading" class="pp-empty">loading…</div>
            <div v-else-if="treeError" class="pp-err">{{ treeError }}</div>
            <div v-else-if="!visibleRows.length" class="pp-empty">no entries</div>
            <div
              v-for="row in visibleRows"
              :key="row.path"
              class="pp-row"
              :class="{ sub: row.depth > 0, on: row.path === activePath }"
              :style="{ paddingLeft: 10 + row.depth * 13 + 'px' }"
              @click="onRow(row)"
            >
              <span class="pp-cv">{{ row.type === 'directory' ? (expanded[row.path] ? '&#9662;' : '&#9656;') : '' }}</span>
              <span class="pp-ico" v-html="row.icon"></span>
              <span class="pp-nm">{{ row.name }}</span>
              <span class="pp-ct">{{ row.type === 'directory' ? '' : row.size }}</span>
            </div>
          </div>
        </div>
        <FilePreview v-if="previewPath" :path="previewPath" @close="previewPath = ''" />
      </div>
      <div class="pp-foot">
        <span class="pp-hint">unassigned sessions are adopted by the active root</span>
      </div>
    </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  adoptUnassigned, addProject,
  refreshProjects, removeProject, searchProjectDir, switchProject, toast, unassignedCount
} from '../data/wire'
import { askConfirm } from '../data/confirm'
import { fileGlyph } from '../data/fileicons'
import { projects } from '../data/store'
import FilePreview from './FilePreview.vue'

/* The registry is the gateway's, not the UI's: every entry is a directory the
   tools are allowed to touch, and `active` is the one they are scoped to. */
const addPath = ref('')
const addError = ref('')
const unassigned = ref(0)
/* The tree has three states, not two: still loading, read refused, and a folder
   with nothing in it. Rendering a refusal as "no entries" is what made a broken
   registry look like an empty project. */
const treeLoading = ref(false)
const treeError = ref('')
/* Which file row is selected, so the tree can show it without asking the
   gateway. Reset on every project switch: it names a path inside the old root. */
const activePath = ref('')
/* The file whose preview pane is mounted. Empty means no pane at all: the
   component is behind a `v-if`, so nothing is read and nothing is held until a
   row is clicked. */
const previewPath = ref('')

/* The popover's `open` class belongs to the runtime, and a class binding on the
   same element rewrites `className` wholesale: the panel disappeared the moment
   a file was clicked, because `open` was patched away with it. Custom properties
   carry the same state and leave that attribute alone. */
const pvStyle = computed<Record<string, string>>(() => ({
  '--pp-w': previewPath.value ? 'min(745px, calc(100vw - 24px))' : '392px',
  '--pp-left': previewPath.value ? '0 0 392px' : '1 1 auto'
}))

const activeName = computed(() => baseName(projects.active) || 'no project')

function baseName(p: string): string {
  const trimmed = p.replace(/[\\/]+$/, '')
  const cut = Math.max(trimmed.lastIndexOf('\\'), trimmed.lastIndexOf('/'))
  return cut >= 0 ? trimmed.slice(cut + 1) : trimmed
}

async function onPick(path: string): Promise<void> {
  if (path === projects.active) return
  const ok = await switchProject(path)
  /* The tree, the highlighted row and the open preview all belong to the root
     they were read from: a path from the previous project names nothing under
     the new one, so the pane goes with the selection. */
  if (ok) {
    activePath.value = ''
    previewPath.value = ''
  }
}

/** Re-reads the registry and reselects the active project, which reloads the
    transcript and the tree in one step. */
async function onRefresh(): Promise<void> {
  await refreshProjects()
  if (projects.active) {
    previewPath.value = ''
    await switchProject(projects.active)
  }
}

async function onAdd(): Promise<void> {
  addError.value = ''
  const res = await addProject(addPath.value)
  if (!res.ok) {
    addError.value = res.error ?? 'could not add this project'
    return
  }
  addPath.value = ''
}

async function onRemove(path: string): Promise<void> {
  /* Removing an entry is not destructive to files, but it is not undoable from
     the panel either, and the row disappears on a single stray click otherwise. */
  const ok = await askConfirm({
    title: 'Remove project?',
    body: `${path} leaves the registry. Sessions keep their records and the folder itself is not touched.`,
    confirmLabel: 'Remove',
    danger: true,
    hint: 'the folder is not deleted'
  })
  if (!ok) return
  const res = await removeProject(path)
  if (!res.ok) {
    toast('Could not remove project', res.error ?? 'The gateway refused this project.', 'err')
    return
  }
  toast('Project removed', `${baseName(path)} left the registry. Sessions keep their records.`, 'warn')
}

async function onAdopt(): Promise<void> {
  if (!projects.active) return
  const res = await adoptUnassigned(projects.active)
  unassigned.value = await unassignedCount()
  if (!res.ok) {
    toast('Adopt failed', res.error ?? 'The gateway could not adopt these sessions.', 'err')
    return
  }
  if (!res.adopted) {
    toast('Nothing to adopt', 'No unassigned sessions were found.', 'warn')
    return
  }
  toast('Sessions adopted', `${res.adopted} unassigned sessions now belong to ${baseName(projects.active)}.`, 'ok')
}

interface TreeRow {
  name: string
  path: string
  type: string
  depth: number
  size: string
  /* Resolved once when the directory is read: the row is a flat list entry, so
     the glyph never has to be rebuilt while the tree is merely re-flattened. */
  icon: string
}

const children = ref<Record<string, TreeRow[]>>({})
const expanded = ref<Record<string, boolean>>({})

/* Flattened view of the expanded directories. Building it on every change keeps
   the markup a single v-for instead of a recursive component, and the project
   popover never holds more than a few hundred rows. */
const visibleRows = computed<TreeRow[]>(() => {
  const out: TreeRow[] = []
  const walk = (key: string): void => {
    for (const row of children.value[key] ?? []) {
      out.push(row)
      if (row.type === 'directory' && expanded.value[row.path]) walk(row.path)
    }
  }
  walk('')
  return out
})

const treeHint = computed(() => {
  const n = visibleRows.value.length
  /* Each level is read with a 200-entry cap, so a full folder means the list is
     a sample rather than the directory. Say so instead of implying a count. */
  const capped = Object.values(children.value).some(rows => rows.length >= 200)
  return capped ? `${n}+ entries` : `${n} entries`
})

async function load(path: string, depth: number): Promise<void> {
  const { rows: list, error } = await searchProjectDir(path, 200)
  if (error) {
    /* A refused read and an empty folder are different answers. Only the root
       owns the tree-level message; a subfolder reports through the toast so the
       rest of the tree stays usable. */
    if (depth === 0) treeError.value = error
    else toast('Could not read folder', error, 'err')
    children.value[path] = []
    return
  }
  const rows: TreeRow[] = list.map(raw => {
    const name = String(raw.name ?? '')
    const type = String(raw.type ?? 'file')
    return {
      name,
      path: String(raw.path ?? ''),
      type,
      depth,
      size: '',
      icon: fileGlyph(name, type)
    }
  })
  children.value[path] = rows
    .filter(r => r.name && r.name !== '__pycache__' && r.name !== '.git')
    .sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'directory' ? -1 : 1))
}

async function onRow(row: TreeRow): Promise<void> {
  if (row.type === 'directory') {
    const next = !expanded.value[row.path]
    expanded.value[row.path] = next
    if (next && !children.value[row.path]) await load(row.path, row.depth + 1)
    return
  }
  /* A file opens in the pane beside the tree, not in whatever program owns the
     extension. That handoff is still one click away, inside the preview header,
     for the formats a text pane cannot show. */
  activePath.value = row.path
  previewPath.value = row.path
}

async function reloadTree(): Promise<void> {
  treeLoading.value = true
  treeError.value = ''
  children.value = {}
  expanded.value = {}
  activePath.value = ''
  previewPath.value = ''
  try {
    await load('', 0)
  } finally {
    treeLoading.value = false
  }
}

/** Registry + adopt counter, both of which the gateway only answers once the
    socket is up. */
async function loadRegistry(): Promise<void> {
  await refreshProjects()
  unassigned.value = await unassignedCount()
}

/** The popover is opened and closed by the runtime, not by this component. Its
    close is the moment the preview has to go: a file's text should not sit in
    memory behind a panel that is no longer on screen. */
function onPanelToggle(event: Event): void {
  const open = (event as CustomEvent<{ open?: boolean }>).detail?.open
  if (!open) previewPath.value = ''
}

/* The pane widens the panel, which moves its right edge; the runtime is what
   keeps that edge inside the window. Cue it once the new width has been laid
   out, otherwise it measures the old one. */
watch(previewPath, () => {
  void nextTick(() => {
    window.dispatchEvent(new CustomEvent('pebble:project-relayout'))
  })
})

function onGatewayReady(): void {
  void reloadTree()
  void loadRegistry()
}

onMounted(() => {
  void reloadTree()
  void loadRegistry()
  // The socket opens after this mounts; the first answer would be empty.
  window.addEventListener('pebble:gateway-ready', onGatewayReady)
  window.addEventListener('pebble:project-panel', onPanelToggle)
})

onUnmounted(() => {
  window.removeEventListener('pebble:gateway-ready', onGatewayReady)
  window.removeEventListener('pebble:project-panel', onPanelToggle)
})
</script>
