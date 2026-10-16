<script setup lang="ts">import{ref,computed}from'vue';import{useMemoryStore}from'../stores/memory';import type{MemoryEntry}from'../api/memory';const e=defineEmits<{close:[];created:[memory:MemoryEntry]}>();const st=useMemoryStore();const mt=ref<'user'|'feedback'|'project'|'reference'>('user');const n=ref('');const d=ref('');const c=ref('');const ti=ref('');const ic=ref(false);const iv=computed(()=>n.value.trim()&&c.value.trim())
async function hc(){if(!iv.value||ic.value)return;ic.value=true;try{const t=ti.value.split(',').map(x=>x.trim()).filter(x=>x.length>0);const entry=await st.createNewMemory(n.value.trim(),mt.value,d.value.trim(),c.value.trim(),t.length>0?t:undefined);if(entry){e('created',entry);rs()}}catch{}finally{ic.value=false}}
function rs(){mt.value='user';n.value='';d.value='';c.value='';ti.value=''}
</script>
<template><div class="mcd-overlay" @click.self="e('close')"><div class="mcd-dialog" @click.stop><div class="mcd-hdr"><div><h2 class="mcd-title">Create New Memory</h2><p class="mcd-subtitle">Capture a new fragment of the digital lineage.</p></div><button class="mcd-close" @click="e('close')"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></div><div class="mcd-body custom-scrollbar"><div class="mcd-field"><label class="mcd-lbl">Memory Type</label><select v-model="mt" class="mcd-select"><option value="user">User</option><option value="feedback">Feedback</option><option value="project">Project</option><option value="reference">Reference</option></select></div><div class="mcd-field"><label class="mcd-lbl">Name</label><input v-model="n" class="mcd-input" placeholder="Enter memory title..." type="text"/></div><div class="mcd-field"><label class="mcd-lbl">Description</label><input v-model="d" class="mcd-input" placeholder="Brief summary" type="text"/></div><div class="mcd-field"><label class="mcd-lbl">Core Content</label><textarea v-model="c" class="mcd-textarea" placeholder="Type detailed memory data here..." rows="6"/></div><div class="mcd-field"><label class="mcd-lbl">Tags (comma-separated)</label><input v-model="ti" class="mcd-input" placeholder="e.g. preferences, history, technical" type="text"/></div></div><div class="mcd-actions"><button class="mcd-cancel" @click="e('close')">Cancel</button><button class="mcd-create" :disabled="ic||!iv" @click="hc">{{ic?'Creating...':'Create'}}</button></div></div></div></template>
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
.mcd-textarea{width:100%;background:var(--bg-input);border:1px solid var(--border);border-radius:4px;padding:10px;font-size:11px;color:var(--text);outline:none;resize:none;font-family:var(--font-mono);line-height:1.5}
.mcd-textarea:focus{border-color:var(--accent)}
.mcd-actions{padding:16px 24px;display:flex;justify-content:flex-end;gap:10px;border-top:1px solid var(--border)}
.mcd-cancel{background:none;border:1px solid var(--border);border-radius:4px;padding:6px 16px;font-size:11px;color:var(--text);cursor:pointer;font-family:var(--font-mono)}
.mcd-cancel:hover{background:var(--bg-hover)}
.mcd-create{background:var(--accent);color:#000;border:none;border-radius:4px;padding:6px 20px;font-size:11px;font-weight:600;cursor:pointer;font-family:var(--font-mono)}
.mcd-create:hover{opacity:.85}
.mcd-create:disabled{opacity:.4;cursor:not-allowed}
</style>
