<script setup lang="ts">import{ref,watch,onMounted}from'vue';import{useConfigStore}from'../stores/config';import{useGatewayStore}from'../stores/gateway';import Tooltip from'./Tooltip.vue';import ConfirmDialog from'./ConfirmDialog.vue';import TreeNodes from'./TreeNodes.vue'
interface FI{name:string;path:string;type:'file'|'directory';depth:number;request:<T>(m:string,p?:any)=>Promise<T>}
const cfg=useConfigStore();const g=useGatewayStore();const o=ref(false);const ri=ref<Omit<FI,'depth'|'request'>[]>([]);const lr=ref(false)
const showProjects=ref(false);const addVal=ref('');const delTarget=ref('');const addRef=ref<HTMLInputElement|null>(null)
const EI:Record<string,string>={py:'Py',ts:'TS',tsx:'TSX',js:'JS',jsx:'JSX',vue:'Vue',rs:'Rs',go:'Go',java:'Java',cpp:'C++',c:'C',h:'H',json:'{}',yaml:'YML',yml:'YML',md:'MD',css:'CSS',html:'HTML',sql:'SQL',sh:'SH',bat:'BAT',xml:'XML',txt:'TXT',toml:'TOML',lock:'LOCK',png:'IMG',jpg:'IMG',jpeg:'IMG',gif:'IMG',svg:'IMG'}
function gi(n:string,t:string):{icon:string;color:string}{if(t==='directory')return{icon:'',color:'var(--accent)'};const e=(n.split('.').pop()||'').toLowerCase();const l=EI[e]||e.toUpperCase();const cc:Record<string,string>={py:'#3572A5',ts:'#3178C6',js:'#F7DF1E',vue:'#4FC08D',json:'#5B5B5B',yaml:'#6B6B6B',md:'#083FA1',rs:'#DEA584',go:'#00ADD8',java:'#B07219',cpp:'#F34B7D',css:'#663399',html:'#E34F26',sh:'#89E051',bat:'#C1C1C1',png:'#8BC34A'};return{icon:l,color:cc[e]||'var(--muted)'}}
async function loadRoot(){lr.value=true;try{const r=await g.request<{results:Omit<FI,'depth'|'request'>[]}>('file.search',{q:'',path:cfg.projectPath,limit:200});ri.value=r.results||[]}catch{ri.value=[]}finally{lr.value=false}}
watch(()=>o.value,v=>{if(v){cfg.loadProjects();cfg.refreshUnassigned();loadRoot()}})
watch(()=>cfg.projectPath,()=>{if(o.value&&!showProjects.value)loadRoot()})
onMounted(()=>{window.addEventListener('project-switched',loadRoot);window.addEventListener('project-adopted',()=>cfg.refreshUnassigned())})
async function switchProject(p:string){await cfg.setActiveProject(p);showProjects.value=false}
async function addProj(){const t=addVal.value.trim();if(!t)return;await cfg.addProject(t);addVal.value='';setTimeout(()=>addRef.value?.focus(),50)}
function confirmDel(){if(delTarget.value){cfg.deleteProject(delTarget.value);delTarget.value=''}}
</script>
<template>
<Tooltip text="Project files" position="bottom"><button class="pp-btn" :class="{active:o}" @click="o=!o"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg></button></Tooltip>
<div v-if="o" class="pp-overlay" @click="o=false"><div class="pp-panel" @click.stop>
<div class="pp-hdr">
<div class="pp-path" style="cursor:pointer" @click="showProjects=!showProjects"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:var(--accent)"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg><span class="pp-path-text">{{cfg.projectPath}}</span><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--muted);flex-shrink:0"><polyline points="6 9 12 15 18 9"/></svg></div>
</div>
<div v-if="showProjects" class="pp-proj-list">
  <div v-for="p in cfg.projects" :key="p" class="pp-proj-item" :class="{active:p===cfg.activeProjectPath}" @click="switchProject(p)">
    <span class="pp-proj-name">{{p}}</span>
    <button v-if="cfg.projects.length>1&&p!==cfg.activeProjectPath" class="pp-proj-del" @click.stop="delTarget=p" aria-label="Delete project">&times;</button>
  </div>
  <div v-if="cfg.unassignedCount>0" class="pp-proj-adopt" @click="cfg.adoptUnassigned()">adopt {{cfg.unassignedCount}} unassigned &rarr; current</div>
  <div class="pp-proj-add"><input ref="addRef" class="pp-edit-input" v-model="addVal" placeholder="add project path..." @keydown.enter="addProj" @keydown.escape="showProjects=false"/><button class="pp-edit-btn" @click="addProj"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button></div>
</div>
<div v-else class="pp-tree"><div v-if="lr" class="ft-loading">Loading...</div>
<TreeNodes v-for="item in ri" :key="item.path" :name="item.name" :path="item.path" :type="item.type" :depth="0"/>
</div></div></div>
<ConfirmDialog :open="delTarget!==''" title="Delete project" :message="`Delete project '${delTarget}'? Existing sessions keep their records.`" confirmLabel="Delete" cancelLabel="Cancel" :danger="true" @confirm="confirmDel" @cancel="delTarget=''"/>
</template>
