<script setup lang="ts">import{ref,onMounted,onUnmounted,reactive}from'vue';import{getGateway}from'../services/gatewayClient'
interface PR{request_id:string;permission_type:string;target:string;reason:string;timestamp:string;session_id:string;level:'low'|'medium'|'high'|'critical'}
const PL:Record<string,string>={'file:read':'Read file','file:write':'Write file','file:delete':'Delete file','exec:command':'Execute command','network:fetch':'Network access','app:launch':'Launch app','clipboard:read':'Read clipboard','clipboard:write':'Write clipboard','screenshot':'Screenshot'}
function ft(t:string):string{return PL[t]||t}
function lc(l:string):string{const m:Record<string,string>={low:'#67c23a',medium:'#e6a23c',high:'#f56c6c',critical:'#dc2626'};return m[l]||'#909399'}
function ll(l:string):string{const m:Record<string,string>={low:'Low risk',medium:'Medium risk',high:'High risk',critical:'Critical risk'};return m[l]||'Unknown'}
const rs=ref<PR[]>([]);const v=ref(false);const rm=reactive<Record<string,boolean>>({});const mm=reactive<Record<string,number>>({});const gw=getGateway()
async function sd(rid:string,ap:boolean){const re=rm[rid]||false;const mi=re?mm[rid]||15:0;try{await gw.request('permission.decide',{request_id:rid,approved:ap,remember_minutes:mi})}catch{};rs.value=rs.value.filter(r=>r.request_id!==rid);if(rs.value.length===0)v.value=false}
function aa(){for(const r of rs.value)sd(r.request_id,true)}
function da(){for(const r of rs.value)sd(r.request_id,false)}
function dc(){if(rs.value.length>0)sd(rs.value[0].request_id,false)}
onMounted(()=>{const h=(e:Event)=>{const d=(e as CustomEvent).detail as {payload:PR}|undefined;if(!d?.payload)return;const rq=d.payload;if(rs.value.some(r=>r.request_id===rq.request_id))return;rs.value.push(rq);v.value=true;rm[rq.request_id]=false;mm[rq.request_id]=15};
// A decision taken on the other client (the phone) has to close the sheet here
// as well, otherwise this window keeps asking about a request that is already settled.
const hr=(e:Event)=>{const d=(e as CustomEvent).detail as {payload:{request_id?:string}}|undefined;const rid=d?.payload?.request_id;if(!rid)return;rs.value=rs.value.filter(r=>r.request_id!==rid);if(rs.value.length===0)v.value=false};
window.addEventListener('gateway:permission-request',h);window.addEventListener('gateway:permission-resolved',hr);onUnmounted(()=>{window.removeEventListener('gateway:permission-request',h);window.removeEventListener('gateway:permission-resolved',hr)})})
</script>
<template><div v-if="v&&rs.length>0" class="pd-overlay"><div class="pd-dialog" @click.stop>
<div class="pd-hdr"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="pd-hdr-icon"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg><span class="pd-hdr-title">Permission Request</span><span class="pd-badge">{{rs.length}}</span></div>
<div class="pd-body"><div v-for="req in rs" :key="req.request_id" class="pd-item">
<div class="pd-type-row"><span class="pd-type-badge" :style="{background:lc(req.level)}">{{ft(req.permission_type)}}</span><span class="pd-level">{{ll(req.level)}}</span></div>
<div class="pd-target"><code>{{req.target}}</code></div><div class="pd-reason"><span class="pd-reason-label">Reason: </span><span class="pd-reason-text">{{req.reason}}</span></div>
<div class="pd-remember"><label class="pd-cb"><input type="checkbox" @change="rm[req.request_id]=($event.target as HTMLInputElement).checked"/><span class="pd-cb-custom"/><span class="pd-cb-label">Remember choice</span></label><select class="pd-select" @change="mm[req.request_id]=parseInt(($event.target as HTMLSelectElement).value)"><option value="5">5 minutes</option><option value="15">15 minutes</option><option value="30">30 minutes</option><option value="60">1 hour</option><option value="1440">1 day</option></select></div></div></div>
<div class="pd-ft"><button class="pd-btn pd-btn-deny" @click="da">Deny All</button><button v-if="rs.length>1" class="pd-btn pd-btn-deny-cur" @click="dc">Deny Current</button><button class="pd-btn pd-btn-allow" @click="aa">Allow All</button></div></div></div></template>
