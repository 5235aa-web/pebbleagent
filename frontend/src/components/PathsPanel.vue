<template>
<div class="overlay" id="sheet-paths">
  <div class="sheet" style="max-width:560px">
    <div class="sheet-hdr">
      <span class="sheet-ico">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
      </span>
      <div class="sheet-title">Path whitelist<small>filesystem boundaries</small></div>
      <button class="sheet-x" data-close>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="sheet-body">
      <div class="mcp-item" v-if="!files.paths.length">
        <span class="mcp-logo">·</span>
        <div class="mcp-main">
          <div class="mcp-name">no path yet</div>
          <div class="mcp-desc">add one below; tool calls are scoped to these roots</div>
        </div>
      </div>
      <div class="mcp-item" v-for="(p, i) in files.paths" :key="p.path">
        <span class="mcp-logo" style="color:var(--gold)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        </span>
        <div class="mcp-main">
          <div class="mcp-name">{{ p.path }}</div>
          <div class="mcp-desc">{{ p.mode }}</div>
        </div>
        <button class="btn btn-ghost btn-sm" @click="startEdit(i)">Edit</button>
        <button class="btn btn-ghost btn-sm" @click="remove(p.path)">Remove</button>
      </div>
      <div class="section-h" v-if="files.rejected.length">invalid entries
        <button class="sh-act" @click="dropRejected">Remove all</button>
      </div>
      <div class="mcp-item" v-for="r in files.rejected" :key="'rej-' + r.path" style="opacity:.65">
        <span class="mcp-logo">!</span>
        <div class="mcp-main">
          <div class="mcp-name">{{ r.path }}</div>
          <div class="mcp-desc">refused on save: {{ r.reason }}</div>
        </div>
      </div>
      <div class="field" style="margin-top:10px">
        <div class="field-lbl"><span>{{ editing >= 0 ? 'replace this path' : 'add path' }}</span></div>        <div class="field-in">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--faint)" stroke-width="1.8"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          <input type="text" :placeholder="editing >= 0 ? (files.paths[editing]?.path ?? '') : 'D:\\projects\\scratch'" v-model="draft" @keydown.enter.prevent="submit" />
          <button class="btn btn-dark btn-sm" @click="submit">{{ editing >= 0 ? 'Update' : 'Add' }}</button>
          <button class="btn btn-ghost btn-sm" v-if="editing >= 0" @click="cancelEdit">Cancel</button>
        </div>
      </div>
    </div>
    <div class="sheet-foot">
      <span class="sf-hint">{{ files.paths.length }}/5 roots · outside these every write is denied</span>
      <button class="btn btn-ghost" data-close>Close</button>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { files } from '../data/panels'
import { addWhitelistPath, dropRejectedPaths, removeWhitelistPath, updateWhitelistPath } from '../data/wire'

const draft = ref('')
/* Index of the row being replaced, or -1 when the field adds a new root. The
   gateway stores plain absolute paths, so "editing" a row replaces the string;
   there is no per-root permission to edit, and the old RWX / R segment that
   pretended otherwise was a local control that changed nothing. */
const editing = ref(-1)

function toast(title: string, message: string, kind: 'ok' | 'warn' | 'err' = 'warn'): void {
  const fn = (window as unknown as { __pebbleToast?: (t: string, m: string, k?: string) => void }).__pebbleToast
  if (fn) fn(title, message, kind)
}

function startEdit(index: number): void {
  editing.value = index
  draft.value = files.paths[index]?.path ?? ''
}

function cancelEdit(): void {
  editing.value = -1
  draft.value = ''
}

function submit(): void {
  if (editing.value >= 0) {
    const res = updateWhitelistPath(editing.value, draft.value)
    if (!res.ok) {
      toast('Not updated', res.error ?? 'The gateway refused the change.', 'warn')
      return
    }
    cancelEdit()
    return
  }
  const res = addWhitelistPath(draft.value)
  if (!res.ok) {
    toast('Not added', res.error ?? 'The gateway refused the change.', 'warn')
    return
  }
  draft.value = ''
}

function remove(path: string): void {
  const res = removeWhitelistPath(path)
  if (!res.ok) {
    toast('Not removed', res.error ?? 'The gateway refused the change.', 'warn')
    return
  }
  if (editing.value >= 0) cancelEdit()
}

/** Invalid rows cannot be deleted one by one: the only write the gateway has is
    "save this whole list", and it refuses a list that still contains one. */
function dropRejected(): void {
  dropRejectedPaths()
  toast('Invalid entries cleared', 'The file now holds only the paths that pass validation.', 'ok')
}
</script>
