<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { useConfigStore } from '../stores/config'

// First-launch guide: shown when no project folder has been chosen yet, so a
// fresh install never silently operates inside the sidecar's working directory.
const p = defineProps<{ open: boolean }>()
const e = defineEmits(['close', 'saved'])

const cfg = useConfigStore()
const val = ref('')
const err = ref('')
const busy = ref(false)
const inp = ref<HTMLInputElement | null>(null)

watch(() => p.open, async v => {
  if (!v) return
  err.value = ''
  busy.value = false
  await nextTick()
  inp.value?.focus()
})

async function submit() {
  if (busy.value) return
  const path = val.value.trim()
  if (!path) { err.value = 'Enter a folder path'; return }
  busy.value = true
  err.value = ''
  const r = await cfg.addProject(path)
  busy.value = false
  if (!r.ok) { err.value = r.error || 'Failed to add project'; return }
  window.dispatchEvent(new CustomEvent('project-switched', { detail: { path: r.path } }))
  e('saved')
}
</script>

<template>
<Teleport to="body">
  <div v-if="p.open" class="cfm-overlay">
    <div class="cfm-dialog psd-dialog" @click.stop>
      <div class="cfm-hdr cfm-hdr-primary">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="flex-shrink:0"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
        <span class="cfm-title">Select project folder</span>
      </div>
      <div class="cfm-body">
        <p class="psd-hint">PebbleAgent reads and writes files inside a project folder. Enter the full path of an existing directory.</p>
        <input
          ref="inp"
          v-model="val"
          class="psd-input"
          placeholder="D:\projects\my-app"
          spellcheck="false"
          autocomplete="off"
          @keydown.enter="submit"
          @keydown.escape="e('close')"
        />
        <div v-if="err" class="psd-err">{{ err }}</div>
      </div>
      <div class="cfm-actions">
        <button class="cfm-btn cfm-btn-cancel" @click="e('close')">Later</button>
        <button class="cfm-btn cfm-btn-primary" :disabled="busy" @click="submit">{{ busy ? 'Saving...' : 'Save' }}</button>
      </div>
    </div>
  </div>
</Teleport>
</template>

<style scoped>
.psd-dialog { max-width: 420px; }
.psd-hint {
  margin: 0 0 10px;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--text-body);
}
.psd-input {
  width: 100%;
  height: 30px;
  padding: 0 9px;
  box-sizing: border-box;
  background: var(--bg-input);
  border: 1px solid var(--border-subtle);
  border-radius: 4px;
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12px;
  outline: none;
  transition: border-color 0.12s;
}
.psd-input:focus { border-color: var(--accent); }
.psd-input::placeholder { color: var(--muted); opacity: 0.55; }
.psd-err {
  margin-top: 8px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.4;
  color: var(--error);
  word-break: break-all;
}
.cfm-btn:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
