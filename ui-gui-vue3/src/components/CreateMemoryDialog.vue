<script setup lang="ts">
import { ref, computed } from 'vue'
import { useMemoryStore } from '../stores/memory'
import type { MemoryEntry, MemoryKind } from '../api/memory'

const emit = defineEmits<{ close: []; created: [memory: MemoryEntry] }>()
const st = useMemoryStore()
const kind = ref<MemoryKind>('correction')
const scope = ref<'project' | 'global'>('project')
const statement = ref('')
const rationale = ref('')
const tools = ref('')
const paths = ref('')
const confidence = ref(0.8)
const creating = ref(false)

const KINDS: Array<{ value: MemoryKind; label: string }> = [
  { value: 'correction', label: 'Correction — 被纠正的做法' },
  { value: 'constraint', label: 'Constraint — 硬性要求' },
  { value: 'preference', label: 'Preference — 稳定偏好' },
  { value: 'pitfall', label: 'Pitfall — 失败过的操作' },
  { value: 'recipe', label: 'Recipe — 可复用解法' },
  { value: 'reference', label: 'Reference — 外部资料' },
]

const valid = computed(() => statement.value.trim().length > 0)

function splitList(raw: string): string[] {
  return raw.split(',').map(x => x.trim()).filter(x => x.length > 0)
}

async function submit() {
  if (!valid.value || creating.value) return
  creating.value = true
  try {
    const entry = await st.createNewMemory(
      kind.value,
      statement.value.trim(),
      rationale.value.trim(),
      { tools: splitList(tools.value), paths: splitList(paths.value) },
      confidence.value,
      scope.value,
    )
    if (entry) {
      emit('created', entry)
      reset()
      emit('close')
    }
  } finally {
    creating.value = false
  }
}

function reset() {
  kind.value = 'correction'
  scope.value = 'project'
  statement.value = ''
  rationale.value = ''
  tools.value = ''
  paths.value = ''
  confidence.value = 0.8
}
</script>

<template>
  <div class="mcd-overlay" @click.self="emit('close')">
    <div class="mcd-dialog" @click.stop>
      <div class="mcd-hdr">
        <div>
          <h2 class="mcd-title">New Experience</h2>
          <p class="mcd-subtitle">记录一条会在后续会话中生效的经验。</p>
        </div>
        <button class="mcd-close" @click="emit('close')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
      <div class="mcd-body custom-scrollbar">
        <div class="mcd-field">
          <label class="mcd-lbl">Kind</label>
          <select v-model="kind" class="mcd-select">
            <option v-for="item in KINDS" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </div>
        <div class="mcd-field">
          <label class="mcd-lbl">Scope</label>
          <select v-model="scope" class="mcd-select">
            <option value="project">Project — 仅当前项目</option>
            <option value="global">Global — 所有项目</option>
          </select>
        </div>
        <div class="mcd-field">
          <label class="mcd-lbl">Statement</label>
          <textarea v-model="statement" class="mcd-textarea" placeholder="一句话结论，例如：不要用原生 title 属性做提示" rows="3" />
        </div>
        <div class="mcd-field">
          <label class="mcd-lbl">Rationale</label>
          <textarea v-model="rationale" class="mcd-textarea" placeholder="为什么这样做，或失败的具体表现" rows="3" />
        </div>
        <div class="mcd-field">
          <label class="mcd-lbl">Tools (comma-separated)</label>
          <input v-model="tools" class="mcd-input" placeholder="e.g. write_file, edit_file" type="text" />
        </div>
        <div class="mcd-field">
          <label class="mcd-lbl">Paths (comma-separated)</label>
          <input v-model="paths" class="mcd-input" placeholder="e.g. *.vue, src/**/*.ts" type="text" />
        </div>
        <div class="mcd-field">
          <label class="mcd-lbl">Confidence</label>
          <div class="mcd-confidence">
            <input v-model.number="confidence" class="mcd-range" type="range" min="0" max="1" step="0.05" />
            <span class="mcd-confidence-val">{{ confidence.toFixed(2) }}</span>
          </div>
        </div>
      </div>
      <div class="mcd-actions">
        <button class="mcd-cancel" @click="emit('close')">Cancel</button>
        <button class="mcd-create" :disabled="creating || !valid" @click="submit">{{ creating ? 'Creating...' : 'Create' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mcd-overlay{position:fixed;inset:0;z-index:9100;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;padding:24px}
.mcd-dialog{background:var(--bg-surface);border:1px solid var(--border);border-radius:8px;box-shadow:0 12px 48px rgba(0,0,0,.5);width:100%;max-width:480px;max-height:90vh;display:flex;flex-direction:column;overflow:hidden;font-family:var(--font-mono);font-size:12px;color:var(--text)}
.mcd-hdr{padding:24px 24px 12px;display:flex;align-items:flex-start;justify-content:space-between}
.mcd-title{font-size:14px;font-weight:600;color:var(--accent);margin:0}
.mcd-subtitle{font-size:10px;color:var(--fg-dim);font-style:italic;margin:4px 0 0}
.mcd-close{background:none;border:none;color:var(--fg-dim);cursor:pointer;padding:4px;border-radius:4px}
.mcd-close:hover{background:var(--bg-hover);color:var(--text)}
.mcd-body{flex:1;overflow-y:auto;padding:8px 24px}
.mcd-field{margin-bottom:16px}
.mcd-lbl{display:block;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--fg-dim);margin-bottom:4px}
.mcd-select,.mcd-input{width:100%;background:var(--bg-input);border:1px solid var(--border);border-radius:4px;padding:8px 10px;font-size:11px;color:var(--text);outline:none;font-family:var(--font-mono)}
.mcd-select:focus,.mcd-input:focus{border-color:var(--accent)}
.mcd-textarea{width:100%;background:var(--bg-input);border:1px solid var(--border);border-radius:4px;padding:10px;font-size:11px;color:var(--text);outline:none;resize:vertical;font-family:var(--font-mono);line-height:1.5}
.mcd-textarea:focus{border-color:var(--accent)}
.mcd-confidence{display:flex;align-items:center;gap:10px}
.mcd-range{flex:1;accent-color:var(--accent)}
.mcd-confidence-val{font-size:11px;color:var(--accent);min-width:32px}
.mcd-actions{padding:16px 24px;display:flex;justify-content:flex-end;gap:10px;border-top:1px solid var(--border)}
.mcd-cancel{background:none;border:1px solid var(--border);border-radius:4px;padding:6px 16px;font-size:11px;color:var(--text);cursor:pointer;font-family:var(--font-mono)}
.mcd-cancel:hover{background:var(--bg-hover)}
.mcd-create{background:var(--accent);color:#000;border:none;border-radius:4px;padding:6px 20px;font-size:11px;font-weight:600;cursor:pointer;font-family:var(--font-mono)}
.mcd-create:hover{opacity:.85}
.mcd-create:disabled{opacity:.4;cursor:not-allowed}
</style>
