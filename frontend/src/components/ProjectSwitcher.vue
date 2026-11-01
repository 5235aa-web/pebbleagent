<template>
    <button class="project-chip tip tip-left" data-tip="Switch project" id="projectChip">
      <span class="pc-icon">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
      </span>
      <span class="pc-name">{{ activeName }}</span>
      <svg class="pc-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
    </button>

    <div class="proj-pop" id="projPop">
      <div class="pp-head">
        <span class="pp-folder">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        </span>
        <div class="pp-cur">
          <div class="pp-name">{{ activeName }}</div>
          <div class="pp-path">{{ projects.active || 'no project selected' }}</div>
        </div>
        <button class="pp-switch" id="ppSwitch" @click="onRescan">Switch
          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
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
        <div v-if="projects.loaded && !projects.list.length" class="pp-empty">no projects registered yet</div>
      </div>

      <button class="pp-adopt" id="ppAdopt" @click="onAdopt">
        <span>adopt <b>{{ unassigned }}</b> unassigned files → current root</span>
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

      <div class="pp-tree-h">files <span>{{ treeHint }}</span>
        <button class="pp-refresh" @click="reloadTree">refresh</button>
      </div>
      <div class="pp-tree">
        <div
          v-for="row in visibleRows"
          :key="row.path"
          class="pp-row"
          :class="{ sub: row.depth > 0, on: row.path === activePath }"
          :style="{ paddingLeft: 10 + row.depth * 13 + 'px' }"
          @click="onRow(row)"
        >
          <span class="pp-cv">{{ row.type === 'directory' ? (expanded[row.path] ? '&#9662;' : '&#9656;') : '' }}</span>
          <span :class="row.type === 'directory' ? 'pp-di' : 'pp-fi'"></span>
          <span class="pp-nm">{{ row.name }}</span>
          <span class="pp-ct">{{ row.type === 'directory' ? '' : row.size }}</span>
        </div>
      </div>
      <div class="pp-foot">
        <span class="pp-hint">unassigned files are adopted by the active root</span>
        <button class="pp-manage" id="ppManage">Manage</button>
      </div>
    </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  adoptUnassigned, addProject, isImagePath, openFile, openImage,
  refreshProjects, removeProject, searchFiles, switchProject, unassignedCount
} from '../data/wire'
import { projects } from '../data/store'

/* The registry is the gateway's, not the UI's: every entry is a directory the
   tools are allowed to touch, and `active` is the one they are scoped to. */
const addPath = ref('')
const addError = ref('')
const unassigned = ref(0)

const activeName = computed(() => baseName(projects.active) || 'no project')

function baseName(p: string): string {
  const trimmed = p.replace(/[\\/]+$/, '')
  const cut = Math.max(trimmed.lastIndexOf('\\'), trimmed.lastIndexOf('/'))
  return cut >= 0 ? trimmed.slice(cut + 1) : trimmed
}

async function onPick(path: string): Promise<void> {
  if (path === projects.active) return
  await switchProject(path)
}

/** Re-reads the registry and reselects the active project, which reloads the
    transcript and the tree in one step. */
async function onRescan(): Promise<void> {
  await refreshProjects()
  if (projects.active) await switchProject(projects.active)
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
  await removeProject(path)
}

async function onAdopt(): Promise<void> {
  if (!projects.active) return
  await adoptUnassigned(projects.active)
  unassigned.value = await unassignedCount()
}

interface TreeRow {
  name: string
  path: string
  type: string
  depth: number
  size: string
}

const children = ref<Record<string, TreeRow[]>>({})
const expanded = ref<Record<string, boolean>>({})
const activePath = ref('')

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

const treeHint = computed(() => visibleRows.value.length + ' entries')

async function load(path: string, depth: number): Promise<void> {
  const list = await searchFiles('', path, 200)
  const rows: TreeRow[] = list.map(raw => ({
    name: String(raw.name ?? ''),
    path: String(raw.path ?? ''),
    type: String(raw.type ?? 'file'),
    depth,
    size: ''
  }))
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
  activePath.value = row.path
  if (isImagePath(row.path)) {
    await openImage(row.path)
    return
  }
  openFile(row.path)
}

async function reloadTree(): Promise<void> {
  children.value = {}
  expanded.value = {}
  await load('', 0)
}

/** Registry + adopt counter, both of which the gateway only answers once the
    socket is up. */
async function loadRegistry(): Promise<void> {
  await refreshProjects()
  unassigned.value = await unassignedCount()
}

onMounted(() => {
  void reloadTree()
  void loadRegistry()
  // The socket opens after this mounts; the first answer would be empty.
  window.addEventListener('pebble:gateway-ready', () => {
    void reloadTree()
    void loadRegistry()
  })
})
</script>
