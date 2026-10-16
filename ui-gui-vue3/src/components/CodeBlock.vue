<script setup lang="ts">import{ref,computed}from'vue'
const p=defineProps<{code:string;language?:string;collapsed?:boolean}>()
const cl=ref(p.collapsed??(p.code||'').split('\n').length>5);const cd=ref(false);const sc=ref(false)
const lc=computed(()=>p.code?p.code.split('\n').length:0)
const hl=computed(()=>{if(!p.code)return'';try{return escap(p.code)}catch{return escap(p.code)}})
function escap(s:string):string{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;')}
async function hc(){try{await navigator.clipboard.writeText(p.code);cd.value=true;setTimeout(()=>cd.value=false,1500)}catch{const ta=document.createElement('textarea');ta.value=p.code;document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);cd.value=true;setTimeout(()=>cd.value=false,1500)}}
</script>
<template><div class="cb-card"><div class="cb-hdr" @click="cl=!cl"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:var(--accent)"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg><span class="cb-lang">{{language||'code'}}</span><span class="cb-lines">{{lc}} lines</span><span class="svg-arrow" :class="{open:!cl}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg></span></div>
<div v-if="!cl" class="cb-body" @mouseenter="sc=true" @mouseleave="sc=false"><pre class="cb-pre"><code v-html="hl"/></pre><button v-if="sc" class="cb-copy-btn" @click="hc" :title="cd?'Copied!':'Copy code'"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>{{cd?' done':''}}</button></div></div></template>
