<template>
<div class="overlay" id="sheet-paths">
  <div class="sheet" style="max-width:540px">
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
      <div class="mcp-item" v-for="(p, i) in files.paths" :key="p.path">
        <span class="mcp-logo" style="color:var(--gold)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        </span>
        <div class="mcp-main">
          <div class="mcp-name">{{ p.path }}</div>
          <div class="mcp-desc">{{ p.mode }}</div>
        </div>
        <div class="seg">
          <button :class="{ on: rights[i] !== 'read' }" @click="rights[i] = 'rwx'">RWX</button>
          <button :class="{ on: rights[i] === 'read' }" @click="rights[i] = 'read'">R</button>
        </div>
      </div>
      <div class="field" style="margin-top:10px">
        <div class="field-lbl"><span>add path</span></div>
        <div class="field-in">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--faint)" stroke-width="1.8"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          <input type="text" placeholder="D:\projects\scratch" v-model="draft" @keydown.enter.prevent="submit" />
          <button class="btn btn-dark btn-sm" @click="submit">Add</button>
        </div>
      </div>
    </div>
    <div class="sheet-foot">
      <span class="sf-hint">outside these roots every write is denied</span>
      <button class="btn btn-ghost" data-close>Close</button>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { files } from '../data/panels'
import { addWhitelistPath } from '../data/wire'

const draft = ref('')
/* Rights are a local display control: the gateway enforces absolute paths, and
   the JSON it stores carries no per-root mode. */
const rights = reactive<Record<number, 'rwx' | 'read'>>({})

function submit() {
  if (addWhitelistPath(draft.value)) draft.value = ''
}
</script>
