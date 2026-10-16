<script setup lang="ts">import{ref,computed,watch}from'vue';import{useMemoryStore}from'../stores/memory';import type{MemoryEntry}from'../api/memory';import CreateMemoryDialog from'./CreateMemoryDialog.vue'
const st=useMemoryStore();const sq=ref('');const sm=ref<MemoryEntry|null>(null);const sd=ref(false);const sv=ref(false);const en=ref('');const ec=ref('');const et=ref<string[]>([]);const eg=ref({user:true,feedback:true,project:true,reference:true})
const gm=computed(()=>{const q=sq.value.toLowerCase();const f=(m:MemoryEntry)=>!q||m.name.toLowerCase().includes(q)||m.content.toLowerCase().includes(q);return{user:st.memories.filter(m=>m.memory_type==='user'&&f(m)),feedback:st.memories.filter(m=>m.memory_type==='feedback'&&f(m)),project:st.memories.filter(m=>m.memory_type==='project'&&f(m)),reference:st.memories.filter(m=>m.memory_type==='reference'&&f(m))}})
async function sl(m:MemoryEntry){sm.value=m;en.value=m.name;et.value=[...(m.tags||[])];try{const f=await st.fetchMemoryByName(m.name);ec.value=f?.content||m.content||''}catch{ec.value=m.content||''}}
async function hs(){if(!sm.value)return;sv.value=true;try{const e=await st.updateMemoryByName(sm.value.name,{content:ec.value,tags:et.value});if(e)sm.value=e}finally{sv.value=false}}
async function hd(){if(!sm.value)return;if(!confirm(`Delete "${sm.value.name}"?`))return;const ok=await st.deleteMemoryByName(sm.value.name);if(ok){sm.value=null;en.value='';ec.value='';et.value=[]}}
function hmc(m:MemoryEntry){sd.value=false;sl(m)}
function hat(){const t=prompt('Enter tag name:');if(t?.trim())et.value.push(t.trim())}
function rt(i:number){et.value.splice(i,1)}
async function has(){const ok=await st.saveAgentMemoryContent();alert(ok?'Saved!':'Failed')}
async function hac(){if(!confirm('Clear agent memory? Cannot undo.'))return;const ok=await st.clearAgentMemoryContent();if(ok)alert('Cleared.')}
function ft(d:string):string{const dd=new Date(d),n=new Date(),diff=n.getTime()-dd.getTime();const h=Math.floor(diff/3600000),days=Math.floor(diff/86400000);if(h<1)return'Just now';if(h<24)return`${h}h ago`;if(days<7)return`${days}d ago`;return dd.toLocaleDateString('en-US',{month:'short',day:'numeric'})}
function fdt(d:string):string{return new Date(d).toLocaleString('en-US',{hour:'2-digit',minute:'2-digit',hour12:true})+' Today'}
const tl:Record<string,string>={user:'User Memory',feedback:'Feedback Memory',project:'Project Memory',reference:'Reference Memory'}
watch(()=>st.agentMemoryScope,()=>{if(st.selectedAgentType)st.fetchAgentMemoryContent()});const wc=computed(()=>st.wordCount)
</script>
<template><Transition name="mem-fade"><div v-if="st.panelOpen" class="mem-overlay" @click.self="st.setPanelOpen(false)"><div class="mem-panel" @click.stop>
<div class="mem-hdr"><div class="mem-hdr-row"><div class="mem-hdr-logo"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg><span class="mem-hdr-title">Memory Archive</span></div><div class="mem-segment"><button :class="['mem-seg-btn',{active:st.mode==='auto'}]" @click="st.switchMode('auto')">Auto Memory</button><button :class="['mem-seg-btn',{active:st.mode==='agent'}]" @click="st.switchMode('agent')">Agent Memory</button></div><button class="mem-close" @click="st.setPanelOpen(false)"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button></div>
<div v-if="st.mode==='auto'" class="mem-stats"><div class="mem-stats-total"><span class="mem-stats-lbl">TOTAL</span><span class="mem-stats-val">{{st.totalMemories}}</span></div><div class="mem-stats-break">
<div class="mem-stat-item"><div class="mem-stat-hdr"><span>USER</span><span>{{st.memoryStats.user}}</span></div><div class="mem-stat-track"><div class="mem-stat-fill" :style="{width:st.memoryStats.userPercent+'%',background:'var(--accent)'}"/></div></div>
<div class="mem-stat-item"><div class="mem-stat-hdr"><span>FEEDBACK</span><span>{{st.memoryStats.feedback}}</span></div><div class="mem-stat-track"><div class="mem-stat-fill" :style="{width:st.memoryStats.feedbackPercent+'%',background:'var(--cyan)'}"/></div></div>
<div class="mem-stat-item"><div class="mem-stat-hdr"><span>PROJECT</span><span>{{st.memoryStats.project}}</span></div><div class="mem-stat-track"><div class="mem-stat-fill" :style="{width:st.memoryStats.projectPercent+'%',background:'var(--green)'}"/></div></div>
<div class="mem-stat-item"><div class="mem-stat-hdr"><span>REF</span><span>{{st.memoryStats.reference}}</span></div><div class="mem-stat-track"><div class="mem-stat-fill" :style="{width:st.memoryStats.referencePercent+'%',background:'var(--fg-dim)'}"/></div></div></div></div>
<div v-else class="mem-agent-info"><div class="mem-agent-info-left"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 3"/></svg><div><div class="mem-agent-info-label">Agent Memory</div><div class="mem-agent-info-desc">Persistent memory for AI agents across sessions</div></div></div><div class="mem-agent-stats"><span>{{wc}} words</span></div></div></div>
<div v-if="st.mode==='auto'" class="mem-body"><div class="mem-sidebar"><div class="mem-sb-hdr"><div class="mem-search"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mem-search-icon"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><input v-model="sq" class="mem-search-input" placeholder="Search memories..." type="text"/></div><button class="mem-new-btn" @click="sd=true"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>New Entry</button></div><div class="mem-sb-body custom-scrollbar">
<template v-for="(group,key) in gm" :key="key"><div v-if="group.length>0" class="mem-group"><button class="mem-group-hdr" @click="(eg as any)[key]=!(eg as any)[key]"><span class="mem-group-title">{{{user:'User Memories',feedback:'Feedback Loops',project:'Project Context',reference:'Reference'}[key]}}</span><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{rotated:!(eg as any)[key]}"><polyline points="6 9 12 15 18 9"/></svg></button><div v-show="(eg as any)[key]" class="mem-group-items"><div v-for="mem in group" :key="mem.name" class="mem-item" :class="{active:sm?.name===mem.name}" @click="sl(mem)"><div class="mem-item-name">{{mem.name}}</div><div class="mem-item-time">{{ft(mem.updated_at)}}</div></div></div></div></template>
<div v-if="st.memories.length===0&&!st.loadingMemories" class="mem-empty"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" opacity="0.3"><path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg><span>No memories yet</span></div>
<div v-if="st.loadingMemories" class="mem-loading"><span class="mem-spin"/><span>Loading...</span></div></div></div>
<div class="mem-editor"><div class="mem-ed-hdr"><div class="mem-ed-title-row"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5" style="opacity:.7"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg><div><h2 class="mem-ed-title">{{sm?'Memory Details':'Select a Memory'}}</h2><p class="mem-ed-subtitle">{{sm?(tl[sm.memory_type]||''):'Choose from list'}}</p></div></div><div v-if="sm" class="mem-ed-actions"><button class="mem-ed-delete" @click="hd">Delete</button><button class="mem-ed-save" :disabled="sv" @click="hs">{{sv?'Saving...':'Save'}}</button></div></div>
<div class="mem-ed-body custom-scrollbar"><div v-if="sm" class="mem-ed-form"><div class="mem-field"><label class="mem-field-lbl primary">Memory Identity</label><input v-model="en" class="mem-field-name" type="text" readonly/></div><div class="mem-field"><label class="mem-field-lbl">Associations</label><div class="mem-tags"><span v-for="(tag,i) in et" :key="i" class="mem-tag"><span class="mem-tag-dot"/>{{tag}}<button class="mem-tag-rm" @click="rt(i)">x</button></span><button class="mem-tag-add" @click="hat">+ Add Tag</button></div></div><div class="mem-field"><label class="mem-field-lbl">Content Narrative</label><div class="mem-content-box"><textarea v-model="ec" class="mem-textarea" placeholder="Content goes here..."/></div></div><div class="mem-meta"><span>Last Sync: {{sm?fdt(sm.updated_at):''}}</span></div></div><div v-else class="mem-no-sel"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" opacity="0.2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg><h3>Select a memory to view</h3></div></div></div></div>
<div v-else class="mem-body"><div class="mem-sidebar"><div class="mem-sb-hdr"><h3 class="mem-agent-sb-title">Select Agent</h3><div class="mem-scope-row"><label class="mem-scope-lbl">Scope:</label><select v-model="st.agentMemoryScope" class="mem-scope-select"><option value="user">User</option><option value="project">Project</option><option value="local">Local</option></select></div></div><div class="mem-sb-body custom-scrollbar"><div v-for="ag in st.agents" :key="ag.name" class="mem-agent-item" :class="{active:st.selectedAgentType===ag.name}" @click="st.selectAgentForMemory(ag.name)"><div class="mem-agent-item-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></div><div class="mem-agent-item-info"><div class="mem-agent-item-name">{{ag.name}}</div><div class="mem-agent-item-desc">{{ag.description||'AI Agent'}}</div></div></div><div v-if="st.agents.length===0" class="mem-empty"><span>No agents</span></div></div></div>
<div class="mem-editor"><div class="mem-ed-hdr"><div class="mem-ed-title-row"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5" style="opacity:.7"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 3"/></svg><div><h2 class="mem-ed-title">{{st.selectedAgentType?`${st.selectedAgentType} Memory`:'Select an Agent'}}</h2><p class="mem-ed-subtitle">Scope: <span class="mem-scope-badge">{{st.agentMemoryScope}}</span></p></div></div><div v-if="st.selectedAgentType" class="mem-ed-actions"><button class="mem-ed-delete" :disabled="!st.agentMemoryContent" @click="hac">Clear</button><button class="mem-ed-save" :disabled="st.savingAgentMemory" @click="has">{{st.savingAgentMemory?'Saving...':'Save'}}</button></div></div><div class="mem-ed-body custom-scrollbar"><div v-if="st.selectedAgentType" class="mem-ed-form"><div class="mem-field"><label class="mem-field-lbl primary">Agent Memory Content</label><p class="mem-field-hint">This memory will be available to the agent across all conversations.</p></div><div class="mem-field"><div class="mem-content-box large"><textarea v-model="st.agentMemoryContent" class="mem-textarea large" placeholder="Enter persistent memory for this agent..."/></div></div><div class="mem-agent-footer"><span>{{st.agentMemoryContent.length}} chars</span><span>{{wc}} words</span><span class="mem-agent-hint">Click "Save" to persist changes</span></div></div><div v-else class="mem-no-sel"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" opacity="0.2"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 3"/></svg><h3>Select an Agent</h3></div></div></div></div></div>
<CreateMemoryDialog v-if="sd" @close="sd=false" @created="hmc"/></div></Transition></template>
<style scoped>
.mem-fade-enter-active,.mem-fade-leave-active{transition:opacity .2s}.mem-fade-enter-from,.mem-fade-leave-to{opacity:0}
.mem-overlay{position:fixed;inset:0;z-index:8000;background:rgba(0,0,0,.5);backdrop-filter:blur(2px);display:flex;align-items:center;justify-content:center;padding:24px}
.mem-panel{background:var(--bg-surface);border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 40px rgba(0,0,0,.4);width:100%;max-width:1100px;max-height:85vh;display:flex;flex-direction:column;overflow:hidden;font-family:var(--font-mono);font-size:12px;color:var(--text)}
.mem-hdr{padding:20px 24px 16px;border-bottom:1px solid var(--border)}
.mem-hdr-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
.mem-hdr-logo{display:flex;align-items:center;gap:8px;color:var(--accent)}
.mem-hdr-title{font-size:14px;font-weight:600;letter-spacing:.5px}
.mem-segment{display:flex;background:var(--bg-input);border-radius:20px;padding:2px}
.mem-seg-btn{padding:5px 14px;border-radius:20px;border:none;background:transparent;color:var(--fg-dim);cursor:pointer;font-size:11px;font-family:var(--font-mono);transition:all .15s}
.mem-seg-btn.active{background:var(--accent);color:#000}
.mem-close{background:none;border:none;color:var(--fg-dim);cursor:pointer;padding:4px;border-radius:4px}
.mem-close:hover{background:var(--bg-hover);color:var(--text)}
.mem-stats{display:flex;gap:32px;align-items:center}
.mem-stats-total{display:flex;flex-direction:column}
.mem-stats-lbl{font-size:9px;text-transform:uppercase;color:var(--fg-dim);letter-spacing:1px}
.mem-stats-val{font-size:22px;font-weight:700;color:var(--accent)}
.mem-stats-break{flex:1;display:flex;gap:12px}
.mem-stat-item{flex:1}
.mem-stat-hdr{display:flex;justify-content:space-between;font-size:8px;text-transform:uppercase;color:var(--fg-dim);margin-bottom:4px}
.mem-stat-track{height:4px;background:var(--bg-input);border-radius:2px;overflow:hidden}
.mem-stat-fill{height:100%;border-radius:2px;transition:width .3s}
.mem-agent-info{display:flex;align-items:center;justify-content:space-between}
.mem-agent-info-left{display:flex;align-items:center;gap:10px}
.mem-agent-info-label{font-size:13px;font-weight:600;color:var(--text)}
.mem-agent-info-desc{font-size:10px;color:var(--fg-dim)}
.mem-agent-stats{font-size:11px;color:var(--accent)}
.mem-body{flex:1;display:grid;grid-template-columns:280px 1fr;overflow:hidden;min-height:0}
.mem-sidebar{display:flex;flex-direction:column;border-right:1px solid var(--border);background:var(--bg-raised);overflow:hidden}
.mem-sb-hdr{padding:12px;border-bottom:1px solid var(--border)}
.mem-search{position:relative;margin-bottom:10px}
.mem-search-icon{position:absolute;left:8px;top:50%;transform:translateY(-50%);color:var(--fg-dim)}
.mem-search-input{width:100%;background:var(--bg-input);border:1px solid var(--border);border-radius:4px;padding:6px 10px 6px 28px;font-size:11px;color:var(--text);outline:none;font-family:var(--font-mono)}
.mem-search-input:focus{border-color:var(--accent)}
.mem-new-btn{width:100%;background:var(--accent);color:#000;border:none;border-radius:4px;padding:6px 10px;font-size:11px;font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;font-family:var(--font-mono)}
.mem-new-btn:hover{opacity:.85}
.mem-sb-body{flex:1;overflow-y:auto;padding:8px}
.mem-group{margin-bottom:6px}
.mem-group-hdr{width:100%;display:flex;align-items:center;justify-content:space-between;padding:6px 10px;background:transparent;border:none;cursor:pointer;color:var(--fg-dim);font-family:var(--font-mono);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.mem-group-hdr:hover{color:var(--text)}
.mem-group-hdr svg{transition:transform .15s}
.mem-group-hdr svg.rotated{transform:rotate(-90deg)}
.mem-group-items{margin-top:2px}
.mem-item{padding:8px 10px;border-radius:4px;cursor:pointer;transition:background .15s;border-left:3px solid transparent}
.mem-item:hover{background:var(--bg-hover)}
.mem-item.active{background:var(--bg-input);border-left-color:var(--accent)}
.mem-item-name{font-size:11px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mem-item-time{font-size:9px;color:var(--fg-dim);margin-top:2px}
.mem-empty,.mem-loading{padding:24px 16px;text-align:center;color:var(--fg-dim);font-size:11px;display:flex;flex-direction:column;align-items:center;gap:8px}
.mem-spin{width:14px;height:14px;border:2px solid var(--fg-dim);border-top-color:var(--accent);border-radius:50%;animation:mem-spin .6s linear infinite;display:inline-block}
@keyframes mem-spin{to{transform:rotate(360deg)}}
.mem-editor{display:flex;flex-direction:column;overflow:hidden}
.mem-ed-hdr{padding:14px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border)}
.mem-ed-title-row{display:flex;align-items:center;gap:10px}
.mem-ed-title{font-size:13px;font-weight:600;color:var(--text);margin:0}
.mem-ed-subtitle{font-size:10px;color:var(--fg-dim);margin:2px 0 0}
.mem-ed-actions{display:flex;gap:8px}
.mem-ed-delete{font-size:10px;color:var(--fg-dim);background:none;border:none;padding:5px 12px;cursor:pointer;font-family:var(--font-mono);border-radius:4px}
.mem-ed-delete:hover{color:var(--red);background:var(--bg-hover)}
.mem-ed-delete:disabled{opacity:.4;cursor:not-allowed}
.mem-ed-save{font-size:10px;font-weight:600;background:var(--accent);color:#000;border:none;padding:5px 16px;border-radius:4px;cursor:pointer;font-family:var(--font-mono)}
.mem-ed-save:hover{opacity:.85}
.mem-ed-save:disabled{opacity:.4;cursor:not-allowed}
.mem-ed-body{flex:1;overflow-y:auto;padding:24px 32px}
.mem-ed-form{max-width:600px}
.mem-field{margin-bottom:28px}
.mem-field-lbl{display:block;font-size:8px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--fg-dim);margin-bottom:6px}
.mem-field-lbl.primary{color:var(--accent)}
.mem-field-hint{font-size:10px;color:var(--fg-dim);margin:4px 0 0;font-style:italic}
.mem-field-name{width:100%;font-size:18px;font-weight:500;border:none;background:transparent;padding:0;color:var(--text);outline:none;font-family:var(--font-mono)}
.mem-tags{display:flex;flex-wrap:wrap;gap:6px}
.mem-tag{display:flex;align-items:center;gap:5px;padding:4px 10px;border-radius:12px;background:var(--bg-input);font-size:10px;color:var(--text)}
.mem-tag-dot{width:5px;height:5px;border-radius:50%;background:var(--accent)}
.mem-tag-rm{background:none;border:none;color:var(--fg-dim);cursor:pointer;font-size:12px;line-height:1;padding:0}
.mem-tag-rm:hover{color:var(--red)}
.mem-tag-add{background:none;border:1px dashed var(--border);border-radius:12px;padding:4px 10px;font-size:10px;color:var(--fg-dim);cursor:pointer;font-family:var(--font-mono)}
.mem-tag-add:hover{border-color:var(--accent);color:var(--accent)}
.mem-content-box{background:var(--bg-input);border:1px solid var(--border);border-radius:6px;padding:20px;min-height:200px}
.mem-content-box.large{min-height:300px}
.mem-textarea{width:100%;background:transparent;border:none;padding:0;font-size:12px;color:var(--text);line-height:1.6;min-height:180px;resize:none;outline:none;font-family:var(--font-mono)}
.mem-textarea.large{min-height:280px}
.mem-meta{padding-top:16px;border-top:1px solid var(--border);font-size:9px;color:var(--fg-dim);font-style:italic}
.mem-agent-footer{padding-top:16px;border-top:1px solid var(--border);display:flex;gap:16px;font-size:10px;color:var(--fg-dim)}
.mem-agent-hint{font-style:italic;margin-left:auto}
.mem-no-sel{text-align:center;padding:60px 0;color:var(--fg-dim)}
.mem-no-sel h3{font-size:14px;font-weight:400;margin:12px 0 0}
.mem-agent-sb-title{font-size:12px;font-weight:600;color:var(--text);margin:0 0 10px}
.mem-scope-row{display:flex;align-items:center;gap:8px}
.mem-scope-lbl{font-size:10px;color:var(--fg-dim)}
.mem-scope-select{flex:1;background:var(--bg-input);border:1px solid var(--border);border-radius:4px;padding:5px 8px;font-size:10px;color:var(--text);cursor:pointer;outline:none;font-family:var(--font-mono)}
.mem-scope-select:focus{border-color:var(--accent)}
.mem-agent-item{display:flex;align-items:center;gap:10px;padding:10px;border-radius:4px;cursor:pointer;transition:background .15s;border-left:3px solid transparent}
.mem-agent-item:hover{background:var(--bg-hover)}
.mem-agent-item.active{background:var(--bg-input);border-left-color:var(--accent)}
.mem-agent-item-icon{width:28px;height:28px;border-radius:50%;background:var(--bg-input);display:flex;align-items:center;justify-content:center;color:var(--accent);flex-shrink:0}
.mem-agent-item-info{flex:1;min-width:0}
.mem-agent-item-name{font-size:11px;font-weight:500;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mem-agent-item-desc{font-size:9px;color:var(--fg-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mem-scope-badge{display:inline-block;padding:1px 6px;background:var(--bg-input);border-radius:3px;font-size:9px;font-weight:600;text-transform:uppercase;color:var(--accent)}
</style>
