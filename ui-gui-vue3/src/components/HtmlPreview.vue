<script setup lang="ts">
// ===== 调试日志：如有渲染问题，请取消下面注释 =====
import{ref,watch,computed,onMounted,onUnmounted}from'vue'
import Tooltip from'./Tooltip.vue'
import{useGatewayStore}from'../stores/gateway'
import{useCommentStore,type HtmlComment}from'../stores/comment'
import{FEEDBACK_CSS,FEEDBACK_JS}from'./feedbackBridgeContent'
import{useConfigStore}from'../stores/config'
import{toPng}from'html-to-image'
const gs=useGatewayStore();const cs=useCommentStore();const cfg=useConfigStore()
// console.log('[HtmlPreview] COMPONENT INIT, projectPath=',cfg.projectPath) // 调试

const p=defineProps<{htmlContent?:string;isStreaming?:boolean;filePath?:string}>()
// console.log('[HtmlPreview] PROPS: filePath=',p.filePath,'htmlContent=',p.htmlContent?p.htmlContent.slice(0,80):'(none)','isStreaming=',p.isStreaming) // 调试

const cl=ref(true);const vm=ref<'preview'|'code'>('preview');const cp=ref(false);const ps=ref(false);const psMsg=ref('');const isTauri=!!(window as any).__TAURI__
const br=ref<HTMLDivElement|null>(null);const ir=ref<HTMLIFrameElement|null>(null);const sr=ref<HTMLDivElement|null>(null)
const bh=ref('0px');const sl=ref(0);const sw=ref(0);const cw=ref(0)
const fbInjected=ref(false);const fbActive=ref(false);const resolvedContent=ref('')
const FILE_RE=/<!--\s*@pebble-file:\s*(\S+)\s*-->/

const effectiveFilePath=computed(()=>{
  if(p.filePath){/*console.log('[HtmlPreview] effectiveFilePath from prop:',p.filePath);*/return p.filePath}
  if(p.htmlContent){const m=p.htmlContent.match(FILE_RE);if(m){/*console.log('[HtmlPreview] effectiveFilePath from comment:',m[1]);*/return m[1]}}
  return null
})

function buildFullPath():string{
  const fp=effectiveFilePath.value||''
  return cfg.projectPath.replace(/\\/g,'/').replace(/\/+$/,'')+'/'+fp.replace(/^\/+/,'')
}

function loadFromFile():void{
  if(!effectiveFilePath.value)return
  const fp=effectiveFilePath.value
  // console.log('[HtmlPreview] loadFromFile fp=',fp) // 调试
  function tryRpc(n:number):void{
    if(n>=2){resolvedContent.value='<html><body style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;color:#888;background:#f5f5f5"><p>Loading...</p></body></html>';return}
    const paths=[fp,cfg.projectPath.replace(/\\/g,'/')+'/'+fp]
    const pth=paths[n]
    // console.log('[HtmlPreview] RPC path=',pth) // 调试
    gs.request<{ok:boolean;text?:string}>('file.read_text',{path:pth})
      .then(r=>{/*console.log('[HtmlPreview] RPC ok=',r?.ok,'len=',r?.text?.length);*/if(r?.ok&&r.text){resolvedContent.value=r.text}else{tryRpc(n+1)}})
      .catch(()=>{tryRpc(n+1)})
  }
  tryRpc(0)
}

const ic=computed(()=>{
  let result:string
  if(resolvedContent.value){result=resolvedContent.value}
  else if(p.htmlContent){result=p.htmlContent.replace(FILE_RE,'').trim()}
  else if(effectiveFilePath.value){result='<html><body style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;color:#888;background:#f5f5f5"><p>Loading...</p></body></html>'}
  else{result='<html><body></body></html>'}
  // console.log('[HtmlPreview] ic: len=',result.length,'fromFile=',!!resolvedContent.value,'first120=',result.slice(0,120)) // 调试
  return result
})

watch(effectiveFilePath,(nv,ov)=>{/*console.log('[HtmlPreview] watch filePath:',ov,'->',nv);*/if(nv)setTimeout(loadFromFile,500)},{immediate:true})
watch(()=>gs.pendingClarify,(nv,ov)=>{/*console.log('[HtmlPreview] watch pendingClarify:',!!ov,'->',!!nv);*/if(nv!==null&&ov===null&&effectiveFilePath.value){reloadIframe()}})
// watch(resolvedContent,(nv)=>{console.log('[HtmlPreview] resolvedContent changed len=',nv.length,'title=',(nv.match(/<title>(.*?)<\/title>/)||[])[1],'bodyBg=',(nv.match(/background:\s*([^;]+)/)||[])[1])}) // 调试

function copyCSS(){cp.value=true;navigator.clipboard.writeText(ic.value);setTimeout(()=>cp.value=false,2000)}
async function downloadPng(){
  const i=ir.value;if(!i)return
  try{
    const d=i.contentDocument||i.contentWindow?.document;if(!d?.body)return
    ps.value=true;psMsg.value='rendering...'
    const el=(d.querySelector('.poster')||d.body)as HTMLElement
    const url=await toPng(el,{pixelRatio:2,backgroundColor:'#000'})
    const fname=(effectiveFilePath.value||'poster').replace('.html','')+'.png'
    if(isTauri){
      const base64=url.split(',')[1]
      const bytes=Uint8Array.from(atob(base64),c=>c.charCodeAt(0))
      const outDir=cfg.projectPath.replace(/\\/g,'/').replace(/\/+$/,'')
      const {writeFile}=await import('@tauri-apps/plugin-fs')
      await writeFile(outDir+'/'+fname,bytes)
      psMsg.value='saved to '+outDir+'/'+fname
    }else{
      const a=document.createElement('a');a.href=url;a.download=fname;a.click()
      psMsg.value='downloaded'
    }
    setTimeout(()=>{ps.value=false;psMsg.value=''},3000)
  }catch(e){
    psMsg.value='failed'
    setTimeout(()=>{ps.value=false;psMsg.value=''},3000)
  }
}
function uh(){const c=sr.value;if(!c)return;sl.value=c.scrollLeft;sw.value=c.scrollWidth;cw.value=c.clientWidth}
function ah(){const i=ir.value;if(!i)return;try{const d=i.contentDocument||i.contentWindow?.document;if(!d?.documentElement)return;const h=d.documentElement.scrollHeight;if(h>100)i.style.height=h+'px'}catch{}}
function es(){const i=ir.value;if(!i)return;try{const d=i.contentDocument||i.contentWindow?.document;if(!d?.head)return;if(d.getElementById('__pebble_scroll'))return;const s=d.createElement('style');s.id='__pebble_scroll';s.textContent=['html, body { overflow: clip !important; height: auto !important; }','* { max-height: none !important; }','body { min-width: 800px !important; }'].join(' ');d.head.appendChild(s)}catch{}}
function fp(){es();requestAnimationFrame(()=>{ah();requestAnimationFrame(()=>{if(br.value)bh.value=br.value.scrollHeight+'px';uh();setTimeout(uh,50);setTimeout(uh,150);setTimeout(uh,300)})})}
function onLoad(){/*const i=ir.value;const doc=i?.contentDocument||i?.contentWindow?.document;console.log('[HtmlPreview] iframe onLoad title=',doc?.title,'bodyLen=',doc?.body?.innerHTML?.length,'bodyBg=',doc?.body?getComputedStyle(doc.body).backgroundColor:'')*/;if(!cl.value){fp();setTimeout(()=>uh(),200);setTimeout(()=>uh(),500)};tryInjectFeedback()}
function tc(){if(cl.value){cl.value=false;requestAnimationFrame(()=>{if(br.value)bh.value=br.value.scrollHeight+'px'});tryInjectFeedback()}else{if(br.value){const h=br.value.scrollHeight;bh.value=h+'px';requestAnimationFrame(()=>bh.value='0px')};setTimeout(()=>cl.value=true,350)}}
watch(()=>[cl.value,ic.value],()=>{if(!cl.value)fp()})

function canActivateFeedback(){return !!(effectiveFilePath.value&&!p.isStreaming&&ir.value)}
function injectFeedbackBridge(){const i=ir.value;if(!i||fbInjected.value)return;/*console.log('[HtmlPreview] injectFeedbackBridge...');*/try{const d=i.contentDocument||i.contentWindow?.document;if(!d?.head||!d?.body)return;const rs=getComputedStyle(document.documentElement);const accent=rs.getPropertyValue('--accent').trim()||'#2563eb';const text=rs.getPropertyValue('--text').trim()||'#1a1a1a';const textBody=rs.getPropertyValue('--text-body').trim()||'#666';const bgCard=rs.getPropertyValue('--bg-card').trim()||'#fff';const bgInput=rs.getPropertyValue('--bg-input').trim()||'#f5f5f5';const bgHover=rs.getPropertyValue('--bg-hover').trim()||'#f0f0f0';const border=rs.getPropertyValue('--border-subtle').trim()||'#e0e0e0';const font=rs.getPropertyValue('--font-mono').trim()||'';const vars=d.createElement('style');vars.textContent=':root{--fb-accent:'+accent+';--fb-text:'+text+';--fb-text-body:'+textBody+';--fb-bg-card:'+bgCard+';--fb-bg-input:'+bgInput+';--fb-bg-hover:'+bgHover+';--fb-border:'+border+';--fb-font:'+font+'}';d.head.appendChild(vars);const st=d.createElement('style');st.textContent=FEEDBACK_CSS;d.head.appendChild(st);const sc=d.createElement('script');sc.textContent=FEEDBACK_JS;d.body.appendChild(sc);fbInjected.value=true;/*console.log('[HtmlPreview] injectFeedbackBridge OK')*/}catch(e){/*console.warn('[HtmlPreview] inject error:',e)*/}}
function activateFeedback(){if(!fbInjected.value)return;fbActive.value=true;const i=ir.value;if(!i?.contentWindow)return;i.contentWindow.postMessage({type:'pebble-feedback-activate'},'*')}
function tryInjectFeedback(){if(!canActivateFeedback())return;injectFeedbackBridge();setTimeout(()=>{if(canActivateFeedback())activateFeedback()},200)}
function handleFbMessage(e:MessageEvent){if(!e.data||typeof e.data!=='object')return;if(e.data.type==='pebble-feedback-update'&&Array.isArray(e.data.comments)){/*console.log('[HtmlPreview] feedback-update comments=',e.data.comments.length);*/cs.clearComments();(e.data.comments as HtmlComment[]).forEach(c=>cs.addComment(c))}}
function handleFileChange(e:Event){const d=(e as CustomEvent).detail;/*console.log('[HtmlPreview] file-change path=',d?.path);*/if(!d?.path||!effectiveFilePath.value)return;const rp=d.path.replace(/\\/g,'/');if(rp.endsWith('/'+effectiveFilePath.value)||effectiveFilePath.value===rp||rp===buildFullPath()){reloadIframe()}}
function reloadIframe(){/*console.log('[HtmlPreview] reloadIframe');*/fbInjected.value=false;fbActive.value=false;cs.clearComments();loadFromFile()}

onMounted(()=>{/*console.log('[HtmlPreview] onMounted');*/window.addEventListener('message',handleFbMessage);window.addEventListener('gateway:file-change',handleFileChange)})
onUnmounted(()=>{/*console.log('[HtmlPreview] onUnmounted');*/window.removeEventListener('message',handleFbMessage);window.removeEventListener('gateway:file-change',handleFileChange)})
</script>

<template><div class="ht-card"><div class="ht-inner" :class="{'is-open':!cl}">
<div class="ht-hdr" @click="tc"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:var(--accent)"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg><span class="ht-title">HTML Preview</span>
<div class="ht-actions" @click.stop>
<Tooltip text="Preview" position="bottom"><button class="icn-btn" :class="{active:vm==='preview'}" @click="vm='preview'"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button></Tooltip>
<Tooltip text="Source" position="bottom"><button class="icn-btn" :class="{active:vm==='code'}" @click="vm='code'"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg></button></Tooltip>
<template v-if="!isStreaming">
<Tooltip text="Copy HTML" position="bottom"><button class="icn-btn" @click="copyCSS"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></Tooltip>
<Tooltip text="Download PNG" position="bottom"><button class="icn-btn" @click="downloadPng"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg></button></Tooltip>
</template></div>
<span v-if="cp" class="ht-badge ht-ok">copied</span>
<span v-if="ps" class="ht-badge ht-png">{{psMsg||'saving...'}}</span>
<span v-if="fbActive" class="ht-badge" style="background:var(--accent);color:#fff">feedback active</span>
<span class="ht-status">{{vm==='preview'?'preview':'source'}}</span>
<span class="svg-arrow" :class="{open:!cl}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg></span></div>
<div ref="br" class="ht-body" :style="{maxHeight:cl?'0px':bh||'400px'}">
<div v-if="vm==='preview'" class="ht-preview-wrapper"><div ref="sr" class="ht-frame-scroll" @scroll="uh"><iframe ref="ir" class="ht-frame" :srcdoc="ic" sandbox="allow-scripts allow-same-origin" title="HTML preview" @load="onLoad"/></div></div>
<pre v-else class="ht-code">{{ic}}</pre></div></div></div></template>
