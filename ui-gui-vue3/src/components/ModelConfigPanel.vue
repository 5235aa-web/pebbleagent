<script setup lang="ts">
import { useConfigStore } from '../stores/config'
import { ref, computed, watch, nextTick } from 'vue'
import type { ReasoningEffort } from '../types/agentChat'
import ConfirmDialog from './ConfirmDialog.vue'
const cfg = useConfigStore()
const sv = ref(false)
const agentPage = ref(0)
const agentsPerPage = 2
const totalAgentPages = computed(() => Math.ceil(cfg.agents.length / agentsPerPage))
function scrollAgents(dir: number) { agentPage.value = Math.max(0, Math.min(totalAgentPages.value - 1, agentPage.value + dir)) }
function doSave(){if(!commitHeaders())return;cfg.saveProviders();sv.value=true;setTimeout(()=>sv.value=false,2000)}
// Request shaping (optional): some gateways need custom headers or accept only
// reasoning_effort. extra headers are edited as JSON text and only committed on
// save, so a half-typed value never reaches the provider config.
const hdrText = ref('')
const hdrErr = ref('')
function syncHdr(){const h=cfg.getActiveProvider()?.extraHeaders;hdrText.value=h&&Object.keys(h).length?JSON.stringify(h):'';hdrErr.value=''}
watch(()=>cfg.activeProviderId,syncHdr,{immediate:true})
watch(()=>cfg.providers,syncHdr)
function onHdrInput(e:Event){hdrText.value=(e.target as HTMLTextAreaElement).value;hdrErr.value=''}
function commitHeaders(){
  const s=hdrText.value.trim()
  if(!s){cfg.updateProvider(cfg.activeProviderId,{extraHeaders:{}});hdrErr.value='';return true}
  try{
    const v=JSON.parse(s)
    if(!v||typeof v!=='object'||Array.isArray(v)){hdrErr.value='must be a JSON object';return false}
    const out:Record<string,string>={}
    for(const [k,val] of Object.entries(v)){
      if(typeof val!=='string'){hdrErr.value='header values must be strings';return false}
      out[k]=val
    }
    cfg.updateProvider(cfg.activeProviderId,{extraHeaders:out});hdrErr.value='';return true
  }catch{hdrErr.value='invalid JSON';return false}
}
// provider 删除：hover 停留 800ms 显示右上角叉叉 → 点击弹确认框
const hoverDel = ref<string | null>(null)
let hoverTimer: ReturnType<typeof setTimeout> | null = null
function hoverIn(id: string) { if (hoverTimer) clearTimeout(hoverTimer); hoverTimer = setTimeout(() => { hoverDel.value = id }, 800) }
function hoverOut() { if (hoverTimer) clearTimeout(hoverTimer); hoverTimer = null; hoverDel.value = null }
const confirmDelId = ref<string | null>(null)
function doDeleteProvider() {
  if (confirmDelId.value) cfg.deleteProvider(confirmDelId.value)
  confirmDelId.value = null
}
// provider tabs 横向滑动：箭头翻看 + 滚轮横滑（与 agent 区一致的交互；+ add 固定在右端不随滚动）
const provScrollRef = ref<HTMLDivElement | null>(null)
const provAtStart = ref(true)
const provAtEnd = ref(false)
function updateProvArrows() {
  const el = provScrollRef.value
  if (!el) return
  provAtStart.value = el.scrollLeft <= 2
  provAtEnd.value = el.scrollLeft + el.clientWidth >= el.scrollWidth - 2
}
function scrollProviders(dir: number) { provScrollRef.value?.scrollBy({ left: dir * 120, behavior: 'smooth' }) }
function onProvWheel(e: WheelEvent) {
  const el = provScrollRef.value
  if (!el || el.scrollWidth <= el.clientWidth) return
  const d = Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX
  if (!d) return
  e.preventDefault()
  el.scrollLeft += d
}
function onAddProvider() {
  cfg.addProvider()
  nextTick(() => {
    const el = provScrollRef.value
    if (el) el.scrollTo({ left: el.scrollWidth, behavior: 'smooth' })
    updateProvArrows()
  })
}
watch(() => cfg.panelOpen, v => { if (v) nextTick(updateProvArrows) })
watch(() => cfg.providers.length, () => nextTick(updateProvArrows))
</script>
<template>
<div class="mcp-overlay" :class="{open:cfg.panelOpen}" @click.self="cfg.setPanelOpen(false)">
  <div class="mcp-dropdown" @click.stop>
    <div class="mcp-body">

      <!-- Agent -->
      <div class="mcp-sh"><div class="mcp-sdot"/><span class="mcp-sl">Agent<span class="mcp-sc">{{cfg.agents.length}}</span></span></div>
      <div class="mcp-agent-wrap">
        <button class="mcp-agent-arrow" :disabled="agentPage===0" @click="scrollAgents(-1)"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg></button>
        <div class="mcp-agent-scroll">
          <div class="mcp-agent-row" :style="{transform:`translateX(-${agentPage*100}%)`}">
            <button v-for="a in cfg.agents" :key="a.name" class="mcp-agent-chip" :class="{active:cfg.activeAgent===a.name}" @click="cfg.setActiveAgent(a.name)"><div class="mcp-agent-name">{{a.displayName}}</div><div class="mcp-agent-desc">{{a.description}}</div></button>
          </div>
        </div>
        <button class="mcp-agent-arrow" :disabled="agentPage>=totalAgentPages-1" @click="scrollAgents(1)"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg></button>
      </div>

      <div class="mcp-div"/>

      <!-- Provider tabs -->
      <div class="mcp-sh"><div class="mcp-sdot"/><span class="mcp-sl">LLM Provider<span class="mcp-sc">{{cfg.providers.length}}</span></span></div>
      <div class="mcp-provider-wrap">
        <button class="mcp-agent-arrow" :disabled="provAtStart" @click="scrollProviders(-1)"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg></button>
        <div class="mcp-provider-scroll" ref="provScrollRef" @scroll="updateProvArrows" @wheel="onProvWheel">
          <div class="mcp-provider-tabs">
            <div v-for="p in cfg.providers" :key="p.id" class="mcp-provider-tab-wrap" @mouseenter="hoverIn(p.id)" @mouseleave="hoverOut">
              <button class="mcp-provider-tab" :class="{active:cfg.activeProviderId===p.id}" @click="cfg.setActiveProviderId(p.id)">{{p.provider}}</button>
              <button v-if="hoverDel===p.id && cfg.providers.length>1" class="mcp-provider-del" @click.stop="confirmDelId=p.id" aria-label="Delete provider">&times;</button>
            </div>
          </div>
        </div>
        <button class="mcp-agent-arrow" :disabled="provAtEnd" @click="scrollProviders(1)"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg></button>
        <button class="mcp-provider-tab add" @click="onAddProvider">+ add</button>
      </div>

      <template v-if="cfg.getActiveProvider()">
        <!-- Form -->
        <div class="mcp-fr"><span class="mcp-fl">name</span><input class="mcp-fi" :value="cfg.getActiveProvider()!.provider" @input="cfg.updateProvider(cfg.activeProviderId,{provider:($event.target as HTMLInputElement).value})"/></div>
        <div class="mcp-fr"><span class="mcp-fl">base url</span><input class="mcp-fi mono" :value="cfg.getActiveProvider()!.baseUrl" @input="cfg.updateProvider(cfg.activeProviderId,{baseUrl:($event.target as HTMLInputElement).value})"/></div>
        <div class="mcp-fr"><span class="mcp-fl">api key</span><input class="mcp-fi mono" type="password" :value="cfg.getActiveProvider()!.apiKey" @input="cfg.updateProvider(cfg.activeProviderId,{apiKey:($event.target as HTMLInputElement).value})"/></div>

        <!-- Models -->
        <div class="mcp-fr"><span class="mcp-fl">models</span>
          <div class="mcp-model-list">
            <div v-for="(mn,i) in cfg.getActiveProvider()!.models" :key="i" class="mcp-model-row" :class="{active:cfg.activeModel[cfg.activeProviderId]===mn}" @click="cfg.setActiveModel(cfg.activeProviderId,mn)">
              <span class="mcp-model-dot"/><input class="mcp-model-inp" :value="mn" @focus="cfg.setActiveModel(cfg.activeProviderId,mn)" @input="cfg.updateModelName(cfg.activeProviderId,mn,($event.target as HTMLInputElement).value)"/>
              <button class="mcp-model-add" @click.stop="cfg.addModel(cfg.activeProviderId,i)" title="Add after">+</button>
              <button class="mcp-model-del" :disabled="cfg.getActiveProvider()!.models.length<=1" @click.stop="cfg.removeModel(cfg.activeProviderId,mn)" title="Remove">&times;</button>
            </div>
          </div>
        </div>

        <!-- Sliders -->
        <div class="mcp-sr"><span class="mcp-fl">temp</span><input type="range" min="0" max="2" step="0.1" :value="cfg.getActiveProvider()!.temperature" @input="cfg.updateProvider(cfg.activeProviderId,{temperature:parseFloat(($event.target as HTMLInputElement).value)})"/><span class="mcp-sv">{{cfg.getActiveProvider()!.temperature.toFixed(1)}}</span></div>
        <div class="mcp-sr"><span class="mcp-fl">top p</span><input type="range" min="0" max="1" step="0.05" :value="cfg.getActiveProvider()!.topP" @input="cfg.updateProvider(cfg.activeProviderId,{topP:parseFloat(($event.target as HTMLInputElement).value)})"/><span class="mcp-sv">{{(cfg.getActiveProvider()!.topP||0).toFixed(1)}}</span></div>
        <div class="mcp-sr"><span class="mcp-fl">max tok</span><input type="range" min="512" max="32000" step="512" :value="cfg.getActiveProvider()!.maxTokens" @input="cfg.updateProvider(cfg.activeProviderId,{maxTokens:parseInt(($event.target as HTMLInputElement).value,10)})"/><span class="mcp-sv">{{(cfg.getActiveProvider()!.maxTokens||0).toLocaleString()}}</span></div>

        <!-- Reasoning -->
        <div class="mcp-fr" :class="{'mcp-dim':cfg.getActiveProvider()?.thinkingEnabled===false}"><span class="mcp-fl">reasoning</span>
          <div class="mcp-reason-row">
            <button v-for="r in (['low','medium','high','max'] as ReasoningEffort[])" :key="r" class="mcp-reason-pill" :class="{active:(cfg.getActiveProvider()!.reasoningEffort||'max')===r}" @click="cfg.updateProvider(cfg.activeProviderId,{reasoningEffort:r})">{{r}}</button>
          </div>
        </div>

        <!-- Thinking 开关（默认开启；关闭时后端发送 thinking:disabled 且不发送强度） -->
        <div class="mcp-fr"><span class="mcp-fl">thinking</span>
          <div class="mcp-tg-row">
            <button class="mcp-tg" :class="{on:cfg.getActiveProvider()?.thinkingEnabled!==false}" @click="cfg.updateProvider(cfg.activeProviderId,{thinkingEnabled:!(cfg.getActiveProvider()?.thinkingEnabled!==false)})"><span class="mcp-tg-knob"/></button>
            <span class="mcp-tg-lb">{{cfg.getActiveProvider()?.thinkingEnabled!==false?'on':'off'}}</span>
          </div>
        </div>

        <!-- 分割线：以下参数仅使用 OpenCode Go 等需要请求整形的网关时才填 -->
        <div class="mcp-sep"><span>opencode-go</span></div>

        <!-- 请求整形（可选）：部分网关要求自定义 header / 只接受 reasoning_effort -->
        <div class="mcp-fr"><span class="mcp-fl">session hdr</span><input class="mcp-fi mono" placeholder="x-opencode-session" :value="cfg.getActiveProvider()!.sessionHeader||''" @input="cfg.updateProvider(cfg.activeProviderId,{sessionHeader:($event.target as HTMLInputElement).value.trim()})"/></div>
        <div class="mcp-fr"><span class="mcp-fl">max effort</span>
          <div class="mcp-reason-row">
            <button v-for="e in (['downgrade','keep'] as ('downgrade'|'keep')[])" :key="e" class="mcp-reason-pill" :class="{active:(cfg.getActiveProvider()!.maxEffort||'downgrade')===e}" @click="cfg.updateProvider(cfg.activeProviderId,{maxEffort:e})">{{e}}</button>
          </div>
        </div>
        <div class="mcp-fr"><span class="mcp-fl">reasoning mode</span>
          <div class="mcp-reason-row">
            <button v-for="m in (['thinking','effort_only'] as const)" :key="m" class="mcp-reason-pill" :class="{active:(cfg.getActiveProvider()!.reasoningMode||'thinking')===m}" @click="cfg.updateProvider(cfg.activeProviderId,{reasoningMode:m})">{{m}}</button>
          </div>
        </div>
        <div class="mcp-fr mcp-fr-top"><span class="mcp-fl">extra headers</span>
          <div class="mcp-hdr">
            <textarea class="mcp-fi mono mcp-hdr-ta" :class="{bad:hdrErr}" :value="hdrText" @input="onHdrInput" placeholder='{"User-Agent": "PebbleAgent/1.0"}'></textarea>
            <span v-if="hdrErr" class="mcp-err">{{hdrErr}}</span>
          </div>
        </div>
      </template>
    </div>

    <!-- Footer -->
    <div class="mcp-foot">
      <button v-if="cfg.getActiveProvider()" class="mcp-foot-del" @click="confirmDelId=cfg.activeProviderId" :disabled="cfg.providers.length<=1">Delete Provider</button>
      <div style="display:flex;align-items:center">
        <button class="mcp-foot-save" @click="doSave">Save to JSON</button>
        <span v-if="sv" class="mcp-saved">saved</span>
      </div>
    </div>
  </div>
</div>
<ConfirmDialog :open="confirmDelId!==null" title="Delete provider" :message="`Delete provider '${cfg.providers.find(p=>p.id===confirmDelId)?.provider || ''}'? This will remove it from the configuration.`" confirmLabel="Delete" cancelLabel="Cancel" :danger="true" @confirm="doDeleteProvider" @cancel="confirmDelId=null"/>
</template>
