<script setup lang="ts">import{ref,computed}from'vue';import{useGatewayStore}from'../stores/gateway'
interface FI{name:string;path:string;type:'file'|'directory'}
const p=defineProps<{name:string;path:string;type:'file'|'directory';depth:number}>()
const g=useGatewayStore();const ex=ref(false);const ch=ref<FI[]|null>(null);const ld=ref(false)
const isD=p.type==='directory'
const EI:Record<string,string>={py:'Py',ts:'TS',tsx:'TSX',js:'JS',jsx:'JSX',vue:'Vue',rs:'Rs',go:'Go',java:'Java',cpp:'C++',c:'C',h:'H',json:'{}',yaml:'YML',yml:'YML',md:'MD',css:'CSS',html:'HTML',sql:'SQL',sh:'SH',bat:'BAT',xml:'XML',txt:'TXT',toml:'TOML',lock:'LOCK',png:'IMG',jpg:'IMG',jpeg:'IMG',gif:'IMG',svg:'IMG'}
const CC:Record<string,string>={py:'#3572A5',ts:'#3178C6',js:'#F7DF1E',vue:'#4FC08D',json:'#5B5B5B',yaml:'#6B6B6B',md:'#083FA1',rs:'#DEA584',go:'#00ADD8',java:'#B07219',cpp:'#F34B7D',css:'#663399',html:'#E34F26',sh:'#89E051',bat:'#C1C1C1',png:'#8BC34A'}
const fi=computed(()=>{if(isD)return{icon:ex.value?'\u25BE':'\u25B8',color:'var(--accent)'};const e=(p.name.split('.').pop()||'').toLowerCase();const l=EI[e]||e.toUpperCase();return{icon:l,color:CC[e]||'var(--muted)'}})
async function tg(){if(!isD){try{await g.request('file.open',{path:p.path})}catch{};return};if(ex.value){ex.value=false;return};if(ch.value===null){ld.value=true;try{const r=await g.request<{results:FI[]}>('file.search',{q:'',path:p.path,limit:100});ch.value=r.results||[]}catch{ch.value=[]};ld.value=false};ex.value=true}
</script>
<template><div><div class="ft-row" :style="{paddingLeft:p.depth*14+6+'px'}" @click="tg"><span v-if="isD" class="ft-arrow" :class="{open:ex}"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg></span><span v-else class="ft-arrow-placeholder"/><span class="ft-icon" :style="{color:fi.color}">{{fi.icon}}</span><span class="ft-name">{{name}}</span><span v-if="ld" class="ft-size" style="opacity:.4">loading...</span></div><div v-if="isD&&ex&&ch"><TreeNodes v-for="c in ch" :key="c.path" :name="c.name" :path="c.path" :type="c.type" :depth="p.depth+1"/></div></div></template>
