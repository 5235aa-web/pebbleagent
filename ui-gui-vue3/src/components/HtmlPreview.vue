<script setup lang="ts">
// ===== 调试日志：如有渲染问题，请取消下面注释 =====
import{ref,watch,computed,onMounted,onUnmounted}from'vue'
import Tooltip from'./Tooltip.vue'
import{useGatewayStore}from'../stores/gateway'
import{useCommentStore,type HtmlComment}from'../stores/comment'
import{FEEDBACK_CSS,FEEDBACK_JS}from'./feedbackBridgeContent'
import{PREVIEW_CORE_JS}from'./previewBridgeContent'
import{useConfigStore}from'../stores/config'
import htmlToImageCode from 'html-to-image/dist/html-to-image.js?raw'
const gs=useGatewayStore();const cs=useCommentStore();const cfg=useConfigStore()
// console.log('[HtmlPreview] COMPONENT INIT, projectPath=',cfg.projectPath) // 调试

const p=defineProps<{htmlContent?:string;isStreaming?:boolean;filePath?:string;dockMode?:boolean}>()
// console.log('[HtmlPreview] PROPS: filePath=',p.filePath,'htmlContent=',p.htmlContent?p.htmlContent.slice(0,80):'(none)','isStreaming=',p.isStreaming) // 调试

const cl=ref(!p.dockMode);const vm=ref<'preview'|'code'>('preview');const cp=ref(false);const ps=ref(false);const psMsg=ref('');const isTauri=typeof window!=='undefined'&&'__TAURI_INTERNALS__' in window
const br=ref<HTMLDivElement|null>(null);const ir=ref<HTMLIFrameElement|null>(null);const sr=ref<HTMLDivElement|null>(null)
const bh=ref('0px');const sl=ref(0);const sw=ref(0);const cw=ref(0)
const fbActive=ref(false);const resolvedContent=ref('')
const FILE_RE=/<!--\s*@pebble-file:\s*(\S+)\s*-->/
// ===== 预览沙箱 =====
// iframe 不带 allow-same-origin（不透明源）：预览脚本无法触达宿主 DOM/storage/WS。
// 宿主与预览仅通过 postMessage 通信；注入的 meta CSP 比主文档更严（connect-src 'none'），
// 预览页不能联网、提交表单或嵌套 iframe。
const PREVIEW_CSP="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' https:; style-src 'unsafe-inline' https:; img-src data: blob: https: http:; font-src data: https:; media-src data: blob: https:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
function escScript(s:string):string{return s.replace(/<\/script/gi,'<\\/script')}
function injectHead(html:string,head:string):string{
  const m=html.match(/^\s*<!doctype[^>]*>/i)
  if(m)return html.slice(0,m[0].length)+head+html.slice(m[0].length)
  const h=html.match(/<head[^>]*>/i)
  if(h)return html.replace(h[0],h[0]+head)
  return head+html
}
function injectTail(html:string,tail:string):string{
  const b=html.match(/<\/body\s*>/i)
  if(b&&b.index!==undefined)return html.slice(0,b.index)+tail+html.slice(b.index)
  const h=html.match(/<\/html\s*>/i)
  if(h&&h.index!==undefined)return html.slice(0,h.index)+tail+html.slice(h.index)
  return html+tail
}
function buildSrcdoc(raw:string):string{
  const head='<meta http-equiv="Content-Security-Policy" content="'+PREVIEW_CSP+'">'
  const scripts='<script>window.__PEBBLE_FEEDBACK_CSS='+escScript(JSON.stringify(FEEDBACK_CSS))+'<\/script>'
    +'<script>'+escScript(htmlToImageCode)+'<\/script>'
    +'<script>'+escScript(PREVIEW_CORE_JS+'\n;\n'+FEEDBACK_JS)+'<\/script>'
  return injectHead(injectTail(raw,scripts),head)
}

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
  return buildSrcdoc(result)
})

watch(effectiveFilePath,(nv,ov)=>{/*console.log('[HtmlPreview] watch filePath:',ov,'->',nv);*/if(ov&&nv!==ov)cs.clearComments();if(nv)setTimeout(loadFromFile,500)},{immediate:true})
watch(()=>gs.pendingClarify,(nv,ov)=>{/*console.log('[HtmlPreview] watch pendingClarify:',!!ov,'->',!!nv);*/if(nv!==null&&ov===null&&effectiveFilePath.value){reloadIframe()}})
// watch(resolvedContent,(nv)=>{console.log('[HtmlPreview] resolvedContent changed len=',nv.length,'title=',(nv.match(/<title>(.*?)<\/title>/)||[])[1],'bodyBg=',(nv.match(/background:\s*([^;]+)/)||[])[1])}) // 调试

function copyCSS(){cp.value=true;navigator.clipboard.writeText(ic.value);setTimeout(()=>cp.value=false,2000)}
// ===== 截图请求（iframe 内执行，postMessage 回传；跨源后宿主无法读取其 DOM）=====
let _capSeq=0;let _capPending:{id:string;resolve:(v:string)=>void;reject:(e:Error)=>void;timer:number}|null=null
function requestCapture(i:HTMLIFrameElement):Promise<string>{
  return new Promise((resolve,reject)=>{
    const id='cap_'+(++_capSeq)
    const timer=window.setTimeout(()=>{if(_capPending&&_capPending.id===id){_capPending=null;reject(new Error('capture timeout'))}},15000)
    _capPending={id,resolve,reject,timer}
    i.contentWindow!.postMessage({type:'pebble-preview-capture',id},'*')
  })
}
async function downloadPng(){
  const i=ir.value;if(!i?.contentWindow)return
  try{
    ps.value=true;psMsg.value='rendering...'
    const url=await requestCapture(i)
    const fname=(effectiveFilePath.value||'poster').replace('.html','')+'.png'
    if(isTauri){
      // 桌面版：经后端 RPC 写入项目目录（复用 WS 通道；路径由后端基于当前项目解析）
      const base64=url.split(',')[1]
      const r=await gs.request<{ok:boolean;path?:string;error?:string}>('file.write_base64',{path:fname,data_base64:base64})
      if(!r?.ok){throw new Error(r?.error||'write failed')}
      psMsg.value='saved to '+(r.path||fname)
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
// 高度与样式由 iframe 内桥接脚本上报/注入（跨源后宿主不可直接测量其 DOM）
function fp(){requestAnimationFrame(()=>{if(br.value)bh.value=br.value.scrollHeight+'px';uh();setTimeout(uh,50);setTimeout(uh,150);setTimeout(uh,300)})}
function onLoad(){if(!cl.value){fp();setTimeout(()=>uh(),200);setTimeout(()=>uh(),500)};tryActivateFeedback()}
function tc(){if(cl.value){cl.value=false;requestAnimationFrame(()=>{if(br.value)bh.value=br.value.scrollHeight+'px'});tryActivateFeedback()}else{if(br.value){const h=br.value.scrollHeight;bh.value=h+'px';requestAnimationFrame(()=>bh.value='0px')};setTimeout(()=>cl.value=true,350)}}
watch(()=>[cl.value,ic.value],()=>{if(!cl.value)fp()})

function canActivateFeedback(){return !!(effectiveFilePath.value&&!p.isStreaming&&ir.value)}
// 评论引擎与 CSS 已随 srcdoc 注入（桥接脚本）；激活时把主题变量发给 iframe
function activateFeedback(){const i=ir.value;if(!i?.contentWindow)return;const rs=getComputedStyle(document.documentElement);const vars:Record<string,string>={'--fb-accent':rs.getPropertyValue('--accent').trim()||'#2563eb','--fb-text':rs.getPropertyValue('--text').trim()||'#1a1a1a','--fb-text-body':rs.getPropertyValue('--text-body').trim()||'#666','--fb-bg-card':rs.getPropertyValue('--bg-card').trim()||'#fff','--fb-bg-input':rs.getPropertyValue('--bg-input').trim()||'#f5f5f5','--fb-bg-hover':rs.getPropertyValue('--bg-hover').trim()||'#f0f0f0','--fb-border':rs.getPropertyValue('--border-subtle').trim()||'#e0e0e0','--fb-font':rs.getPropertyValue('--font-mono').trim()||''};i.contentWindow.postMessage({type:'pebble-feedback-activate',vars},'*');fbActive.value=true}
function tryActivateFeedback(){if(!canActivateFeedback())return;activateFeedback()}
// 预览 iframe 消息统一入口（校验来源窗口身份；不透明源下 e.origin 为 "null"）
function handlePreviewMessage(e:MessageEvent){const i=ir.value;if(!i||e.source!==i.contentWindow)return;const d=e.data as Record<string,unknown>|undefined;if(!d||typeof d!=='object')return;const t=d.type as string
  if(t==='pebble-preview-height'){if(p.dockMode)return;const h=Number(d.height)||0;if(h>100){i.style.height=h+'px';if(!cl.value&&br.value)bh.value=br.value.scrollHeight+'px'}}
  else if(t==='pebble-preview-capture-result'){const p=_capPending;if(p&&d.id===p.id){_capPending=null;window.clearTimeout(p.timer);if(d.ok&&typeof d.dataUrl==='string')p.resolve(d.dataUrl);else p.reject(new Error(String(d.error||'capture failed')))}}
  else if(t==='pebble-feedback-update'&&Array.isArray(d.comments)){cs.clearComments();(d.comments as HtmlComment[]).forEach(c=>cs.addComment(c))}
}
function handleFileChange(e:Event){const d=(e as CustomEvent).detail;/*console.log('[HtmlPreview] file-change path=',d?.path);*/if(!d?.path||!effectiveFilePath.value)return;const rp=d.path.replace(/\\/g,'/');if(rp.endsWith('/'+effectiveFilePath.value)||effectiveFilePath.value===rp||rp===buildFullPath()){reloadIframe()}}
function reloadIframe(){fbActive.value=false;cs.clearComments();loadFromFile()}

onMounted(()=>{window.addEventListener('message',handlePreviewMessage);window.addEventListener('gateway:file-change',handleFileChange)})
onUnmounted(()=>{window.removeEventListener('message',handlePreviewMessage);window.removeEventListener('gateway:file-change',handleFileChange)})
</script>

<template><div class="ht-card" :class="{'is-dock':p.dockMode}"><div class="ht-inner" :class="{'is-open':!cl}">
<div class="ht-hdr" @click="!p.dockMode&&tc()"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:var(--accent)"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg><span class="ht-title">{{ p.dockMode && effectiveFilePath ? effectiveFilePath : 'HTML Preview' }}</span>
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
<span v-if="!p.dockMode" class="svg-arrow" :class="{open:!cl}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg></span></div>
<div ref="br" class="ht-body" :style="{maxHeight:cl?'0px':(p.dockMode?'none':(bh||'400px'))}">
<div v-if="vm==='preview'" class="ht-preview-wrapper"><div ref="sr" class="ht-frame-scroll" @scroll="uh"><iframe ref="ir" class="ht-frame" :srcdoc="ic" sandbox="allow-scripts" title="HTML preview" @load="onLoad"/></div></div>
<pre v-else class="ht-code">{{ic}}</pre></div></div></div></template>
