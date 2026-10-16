<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { getGateway } from '../services/gatewayClient'
import Tooltip from './Tooltip.vue'
type PermMode = 'default' | 'autoAllow'
const MODE_LABEL: Record<PermMode, string> = { default: 'Default', autoAllow: 'Auto Allow' }
const MODE_DESC: Record<PermMode, string> = { default: 'Check by rules; ask when needed', autoAllow: 'Auto-approve all requests without prompting' }
const mode = ref<PermMode>('default'); const open = ref(false); const loading = ref(false)
const gw = getGateway()
async function load() { try { const res = await gw.request<{ mode: string }>('permission.mode.get', {}); if (res.mode === 'autoAllow' || res.mode === 'default') mode.value = res.mode } catch {} }
function toggle() { open.value = !open.value; if (open.value) load() }
function hmousedown(e: MouseEvent) { if (!open.value) return; const t = e.target as HTMLElement; if (!t.closest('.pm-panel') && !t.closest('.pm-btn')) open.value = false }
onMounted(() => { load(); document.addEventListener('mousedown', hmousedown) }); onUnmounted(() => document.removeEventListener('mousedown', hmousedown))
async function selectMode(m: PermMode) { loading.value = true; try { await gw.request('permission.mode.set', { mode: m }); mode.value = m } catch {} finally { loading.value = false; open.value = false } }
const iconPath = computed(() => mode.value === 'autoAllow' ? 'M18 11V8a6 6 0 0 0-12 0v3m12 0a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2h12zm-6 4v2' : 'M12 2l7 3v7c0 4-3.5 7-7 9-3.5-2-7-5-7-9V5l7-3z M12 12m-2 0a2 2 0 100 4 2 2 0 000-4')
import { computed } from 'vue'
</script>
<template>
  <div class="pm">
    <Tooltip :text="`Permission Mode: ${MODE_LABEL[mode]}`"><button class="pm-btn" :class="{active:open,'auto-allow':mode==='autoAllow'}" @click="toggle"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path :d="iconPath"/></svg></button></Tooltip>
    <span class="pm-label">autority</span>
    <div v-if="open" class="pm-panel">
      <div class="pm-hdr"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 2l7 3v7c0 4-3.5 7-7 9-3.5-2-7-5-7-9V5l7-3z"/></svg><span class="pm-title">Permission Mode</span></div>
      <div class="pm-body">
        <button class="pm-opt" :class="{active:mode==='default'}" @click="selectMode('default')" :disabled="loading"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg><div class="pm-opt-info"><span class="pm-opt-label">{{MODE_LABEL.default}}</span><span class="pm-opt-desc">{{MODE_DESC.default}}</span></div></button>
        <button class="pm-opt" :class="{active:mode==='autoAllow'}" @click="selectMode('autoAllow')" :disabled="loading"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M18 11V8a6 6 0 0 0-12 0v3m12 0a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2h12z"/></svg><div class="pm-opt-info"><span class="pm-opt-label">{{MODE_LABEL.autoAllow}}</span><span class="pm-opt-desc">{{MODE_DESC.autoAllow}}</span></div></button>
        <div v-if="mode==='autoAllow'" class="pm-hint"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><span>In Auto Allow mode, all tool-execution requests are approved automatically</span></div>
      </div>
    </div>
  </div>
</template>
