<script setup lang="ts">import{ref,computed}from'vue';import{useGatewayStore}from'../stores/gateway';import{useConfigStore}from'../stores/config';import type{FileChangeItem,DiffHunk}from'../types/agentChat'
const g=useGatewayStore();const cfg=useConfigStore();const po=ref(false);const rf=ref<Set<string>>(new Set())
const ac=computed(()=>g.fileChanges.filter(f=>!f.reverted));const tc=computed(()=>ac.value.length)
function ol(t:string):string{switch(t){case'create':return'NEW';case'delete':return'DEL';case'modify':return'MOD';default:return t.toUpperCase()}}
function oc(t:string):string{switch(t){case'create':return'var(--success)';case'delete':return'var(--error)';case'modify':return'var(--accent)';default:return'var(--muted)'}}
async function te(item:FileChangeItem){if(item.expanded){g.fileChanges=g.fileChanges.map(f=>f.id===item.id?{...f,expanded:false}:f);return};g.fileChanges=g.fileChanges.map(f=>f.id===item.id?{...f,expanded:true,loadingDiff:true}:f);try{const r=await g.request<any>('session.file_diff',{session_id:g.sessionId,relative_path:item.relativePath,project_path:cfg.projectPath});if(r?.hunks?.length>0){const ac=r.hunks.reduce((s:number,h:DiffHunk)=>s+h.lines.filter((l:any)=>l.prefix==='+').length,0);const dc=r.hunks.reduce((s:number,h:DiffHunk)=>s+h.lines.filter((l:any)=>l.prefix==='-').length,0);g.fileChanges=g.fileChanges.map(f=>f.id===item.id?{...f,loadingDiff:false,status:r.status,hunks:r.hunks,addedLines:ac,removedLines:dc}:f)}else{g.fileChanges=g.fileChanges.map(f=>f.id===item.id?{...f,loadingDiff:false,status:'error',hunks:[]}:f)}}catch{g.fileChanges=g.fileChanges.map(f=>f.id===item.id?{...f,loadingDiff:false,status:'error',hunks:[]}:f)}}
async function hr(item:FileChangeItem){rf.value=new Set(rf.value).add(item.id);try{const r=await g.request<any>('session.file_revert',{session_id:g.sessionId,relative_path:item.relativePath});if(r?.success){if(r.remaining===0){g.fileChanges=g.fileChanges.filter(f=>f.id!==item.id)}else{g.fileChanges=g.fileChanges.map(f=>f.id===item.id?{...f,expanded:false,hunks:undefined,addedLines:undefined,removedLines:undefined}:f)}}else{g.fileChanges=g.fileChanges.filter(f=>f.id!==item.id)}}catch{}finally{const nx=new Set(rf.value);nx.delete(item.id);rf.value=nx}}
</script>
<template>
  <div v-if="tc>0||po" class="file-changes-panel">
    <div class="fc-bar" :class="{'fc-bar-open':po}" @click="po=!po">
      <span class="fc-bar-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline :points="po?'6 9 12 15 18 9':'9 18 15 12 9 6'"/></svg></span>
      <span v-if="tc>0" class="fc-bar-text"><span class="fc-bar-count">{{tc}}</span> files changed</span>
      <span v-else class="fc-bar-text fc-bar-text-dim">No file changes</span>
      <template v-if="po&&tc>0"><span class="fc-bar-hint">Click a file to expand the diff; click ↺ to revert</span>
        <button class="fc-accept-btn" @click.stop="async()=>{if(g.sessionId)try{await g.request('session.file_accept',{session_id:g.sessionId})}catch{};g.fileChanges=[];po=false}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg><span>Accept</span></button>
      </template>
    </div>
    <div v-if="po" class="fc-list">
      <div v-if="ac.length===0" class="fc-empty">No file changes in this session</div>
      <div v-for="item in ac" :key="item.id" class="fc-item" :class="{'fc-item-expanded':item.expanded}">
        <div class="fc-item-row" @click="te(item)">
          <span class="fc-item-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></span>
          <span class="fc-item-path">{{item.relativePath}}</span><span class="fc-item-op" :style="{color:oc(item.operationType)}">{{ol(item.operationType)}}</span>
          <span v-if="item.addedLines!==undefined||item.removedLines!==undefined" class="fc-item-stats"><span v-if="item.addedLines&&item.addedLines>0" class="fc-stat-add">+{{item.addedLines}}</span><span v-if="item.removedLines&&item.removedLines>0" class="fc-stat-del">-{{item.removedLines}}</span></span>
          <span class="fc-item-expand-icon"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline :points="item.expanded?'6 9 12 15 18 9':'9 18 15 12 9 6'"/></svg></span>
          <button class="fc-revert-btn" :disabled="rf.has(item.id)" @click="(e:MouseEvent)=>{e.stopPropagation();hr(item)}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg><span>Revert</span></button>
        </div>
        <div v-if="item.expanded" class="fc-diff">
          <div v-if="item.loadingDiff" class="fc-diff-loading">Loading diff...</div>
          <template v-else-if="item.hunks&&item.hunks.length>0">
            <div v-for="(h,hi) in item.hunks" :key="hi" class="fc-diff-hunk">
              <div class="fc-diff-hunk-header">@@ -{{h.old_start}},{{h.old_lines}} +{{h.new_start}},{{h.new_lines}} @@</div>
              <div v-for="(l,li) in h.lines" :key="li" class="fc-diff-line" :class="{'fc-diff-add':l.prefix==='+','fc-diff-del':l.prefix==='-'}"><span class="fc-diff-prefix">{{l.prefix}}</span><span class="fc-diff-text">{{l.text}}</span></div>
            </div>
          </template>
          <div v-else class="fc-diff-empty">{{item.status==='error'?'Failed to load diff':'No changes'}}</div>
        </div>
      </div>
    </div>
  </div>
</template>
<style scoped>
.file-changes-panel{border-top:1px solid var(--border-subtle);background:var(--bg-surface);font-family:var(--font-mono);font-size:12px;flex-shrink:0}
.fc-bar{display:flex;align-items:center;gap:8px;padding:6px 12px;cursor:pointer;user-select:none;color:var(--text-body);transition:background .15s}
.fc-bar:hover{background:var(--bg-hover)}
.fc-bar-open{border-bottom:1px solid var(--border-subtle)}
.fc-bar-icon{display:flex;align-items:center;color:var(--muted)}
.fc-bar-text{font-size:12px;font-weight:500}
.fc-bar-text-dim{opacity:.5}
.fc-bar-count{color:var(--accent);font-weight:700}
.fc-bar-hint{margin-left:auto;font-size:10px;opacity:.4}
.fc-list{max-height:360px;overflow-y:auto;padding:4px 8px 8px}
.fc-empty{padding:16px;text-align:center;color:var(--muted);font-size:10px;opacity:.6}
.fc-item{border-radius:4px;margin-bottom:2px;transition:background .12s}
.fc-item:hover{background:var(--bg-hover)}
.fc-item-expanded{background:var(--bg-input);border:1px solid var(--border-subtle)}
.fc-item-row{display:flex;align-items:center;gap:6px;padding:4px 8px;cursor:pointer;border-radius:4px;min-height:28px}
.fc-item-icon{display:flex;align-items:center;color:var(--muted);flex-shrink:0}
.fc-item-path{flex:1;font-size:11px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fc-item-op{font-size:10px;font-weight:700;flex-shrink:0;letter-spacing:.5px}
.fc-item-stats{display:flex;gap:4px;flex-shrink:0;font-size:10px;font-weight:600}
.fc-stat-add{color:var(--success)}
.fc-stat-del{color:var(--error)}
.fc-item-expand-icon{display:flex;align-items:center;color:var(--muted);flex-shrink:0}
.fc-revert-btn{display:flex;align-items:center;gap:3px;padding:2px 6px;border:1px solid var(--border-subtle);border-radius:3px;background:transparent;color:var(--muted);font-size:10px;cursor:pointer;font-family:inherit;transition:all .12s;flex-shrink:0}
.fc-revert-btn:hover:not(:disabled){border-color:var(--error);color:var(--error);background:rgba(239,83,80,.08)}
.fc-revert-btn:disabled{opacity:.4;cursor:not-allowed}
.fc-accept-btn{display:flex;align-items:center;gap:4px;margin-left:auto;padding:2px 8px;border:1px solid var(--border-subtle);border-radius:3px;background:transparent;color:var(--muted);font-size:10px;cursor:pointer;font-family:inherit;transition:all .12s;flex-shrink:0}
.fc-accept-btn:hover{border-color:var(--success);color:var(--success);background:rgba(123,201,107,.08)}
.fc-diff{border-top:1px solid var(--border-subtle);padding:4px 8px 4px 20px;max-height:300px;overflow-y:auto}
.fc-diff-loading{padding:8px;text-align:center;color:var(--muted);font-size:10px}
.fc-diff-empty{padding:8px;text-align:center;color:var(--muted);font-size:10px;opacity:.6}
.fc-diff-hunk{margin-bottom:4px}
.fc-diff-hunk-header{padding:2px 6px;font-size:10px;color:var(--muted);background:var(--bg-input);border-radius:2px;margin-bottom:2px}
.fc-diff-line{display:flex;font-size:11px;line-height:1.5;padding:0 4px;border-radius:1px}
.fc-diff-add{background:rgba(123,201,107,.12);color:var(--success)}
.fc-diff-del{background:rgba(239,83,80,.12);color:var(--error)}
.fc-diff-prefix{width:14px;flex-shrink:0;text-align:center;opacity:.6}
.fc-diff-text{flex:1;white-space:pre-wrap;word-break:break-all}
</style>
