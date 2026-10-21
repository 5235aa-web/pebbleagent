<script setup lang="ts">import{computed}from'vue';import{parseContent}from'../utils/contentParser';import CodeBlock from'./CodeBlock.vue';import HtmlPreview from'./HtmlPreview.vue';import SvgPreviewCard from'./SvgPreviewCard.vue';import MermaidCard from'./MermaidCard.vue';import{useGatewayStore}from'../stores/gateway';import{normalizePreviewPath,usePreviewStore}from'../stores/preview'
const g=useGatewayStore()
const pv=usePreviewStore()
const p=defineProps<{content:string}>()
// The dock owns the only live preview iframe (see stores/preview.ts); in-flow
// cards would race it for the global comment store.
function openPreview(fp:string):void{pv.setActive(fp)}
function isActivePreview(fp:string):boolean{return pv.activePath===normalizePreviewPath(fp)}
const blocks=computed(()=>parseContent(p.content))
const URL_RE=/(https?:\/\/[^\s<>"'`「」\]\)，。、；：！？\u3000-\u303f\uff00-\uffef]+)/gi
const WWW_RE=/(www\.[^\s<>"'`「」\]\)，。、；：！？\u3000-\u303f\uff00-\uffef]+)/gi
function linkifyBare(text:string):string{let t=text.replace(URL_RE,'<a class="mc-link" href="$1" data-external="1">$1</a>');t=t.replace(WWW_RE,'<a class="mc-link" href="$1" data-external="1">$1</a>');return t}
function rim(line:string):string{let t=line.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');t=t.replace(/\[([^\]]+)\]\(([^)]+)\)/g,(_,label,url)=>{return`<a class="mc-link" href="${url}" data-external="1">${label}</a>`});const parts=t.split(/(<a\b[^>]*>.*?<\/a>)/g);for(let i=0;i<parts.length;i++){if(i%2===0)parts[i]=linkifyBare(parts[i])}t=parts.join('');t=t.replace(/\*\*(.+?)\*\*/g,'<strong style="color:var(--text)">$1</strong>').replace(/__(.+?)__/g,'<strong style="color:var(--text)">$1</strong>');t=t.replace(/`([^`]+)`/g,'<code class="mc-inline-code">$1</code>');return t}
// 行级渲染缓存：超长文本流式期间已定稿的行命中缓存（字符串不变 → v-html prop 相同 → DOM 不重建），
// 每帧仅重算正在增长的最后一行；上限 3000 行防内存膨胀
const lineCache=new Map<string,string>()
function rimCached(line:string):string{let v=lineCache.get(line);if(v===undefined){v=rim(line);if(lineCache.size>3000)lineCache.clear();lineCache.set(line,v)}return v}
function onLinkClick(e:MouseEvent){let el=e.target as HTMLElement;while(el&&el.tagName!=='A')el=el.parentElement as HTMLElement;if(!el||!el.hasAttribute('data-external'))return;e.preventDefault();e.stopImmediatePropagation();const href=el.getAttribute('href')||'';if(!href)return;let url=href;if(!/^https?:\/\//i.test(url))url='https://'+url;g.request('file.open',{path:url}).catch(()=>{})}
</script>
<template><div class="mc" @click.capture="onLinkClick">
  <template v-for="(b,i) in blocks" :key="i">
    <template v-if="b.type==='text'"><p class="mc-text"><span v-for="(line,j) in b.content.split('\n')" :key="j"><span v-if="j>0"><br/></span><span v-html="rimCached(line)"/></span></p></template>
    <CodeBlock v-else-if="b.type==='code'" :code="b.content" :language="b.language" :collapsed="!!(b.lines&&b.lines>8)"/>
    <CodeBlock v-else-if="b.type==='incomplete-code'" :code="b.content" :language="b.language" :collapsed="false"/>
    <HtmlPreview v-else-if="b.type==='html'" :html-content="b.content"/>
    <HtmlPreview v-else-if="b.type==='incomplete-html'" :html-content="b.content"/>
    <button v-else-if="b.type==='html-file'" class="mc-html-ref" :class="{'is-active':pv.dockOpen&&isActivePreview(b.content)}" @click="openPreview(b.content)">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
      <span class="mc-html-ref-name">{{ b.content }}</span>
      <span class="mc-html-ref-hint">{{ pv.dockOpen&&isActivePreview(b.content) ? 'in dock' : 'open' }}</span>
    </button>
    <SvgPreviewCard v-else-if="b.type==='svg'" :code="b.content"/>
    <SvgPreviewCard v-else-if="b.type==='incomplete-svg'" :code="b.content" :is-streaming="true"/>
    <MermaidCard v-else-if="b.type==='mermaid'" :code="b.content" :id="b.id||String(i)"/>
    <div v-else-if="b.type==='incomplete-mermaid'" class="mc-mermaid-loading"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg><span>Generating mermaid diagram...</span></div>
  </template>
</div></template>

<style scoped>
.mc-html-ref {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  margin-top: 6px;
  padding: 7px 10px;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  background: var(--bg-card);
  color: var(--text-body);
  font-family: var(--font-mono);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.mc-html-ref:hover {
  background: var(--bg-hover);
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  color: var(--text);
}
.mc-html-ref.is-active {
  background: color-mix(in srgb, var(--accent) 8%, transparent);
  border-color: color-mix(in srgb, var(--accent) 55%, transparent);
  color: var(--accent);
}
.mc-html-ref-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mc-html-ref-hint {
  flex-shrink: 0;
  font-size: 10px;
  opacity: 0.65;
}
</style>
