<template>
<div class="overlay perm-ov" :class="{ open: permissions.open }" id="permDialog">
  <div class="pg" role="dialog" aria-label="Permission request">
    <header class="pg-hdr">
      <span class="pg-shield">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/></svg>
      </span>
      <div class="pg-head">
        <div class="pg-title">Permission Request<span class="pg-count" id="pgCount">{{ permissions.queue.length }}</span></div>
        <div class="pg-sum">
          <span class="pg-risk critical"><i></i>{{ risk.critical }} critical</span>
          <span class="pg-risk high"><i></i>{{ risk.high }} high</span>
          <span class="pg-risk low"><i></i>{{ risk.low }} low</span>
          <span class="pg-sub">&middot; the agent is paused</span>
        </div>
      </div>
      <button class="sheet-x" data-close>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </header>

    <div class="pg-body" id="pgBody">
      <article v-for="r in permissions.queue" :key="r.id" class="pass"
               :class="{ stamped: r.settled, deny: r.verdict === 'denied', out: r.leaving }"
               :data-level="r.level" :data-id="r.id">
        <span class="pass-edge" aria-hidden="true"></span>
        <div class="pass-top">
          <span class="pass-ico">
            <svg v-if="r.icon === 'command'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
            <svg v-else-if="r.icon === 'write'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg>
          </span>
          <span class="pass-type">{{ r.kind }}</span>
          <span class="pass-lvl">{{ levelLabel(r.level) }}</span>
          <span class="pass-acts">
            <button class="pass-btn deny" data-act="deny" data-tip="Deny this request">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
            <button class="pass-btn allow" data-act="allow" data-tip="Allow this request">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="20 6 9 17 4 12"/></svg>
            </button>
          </span>
        </div>
        <div class="pass-target"><span class="pt-key">{{ r.targetKey }}</span><code>{{ r.target }}</code></div>
        <div class="pass-reason"><span class="pr-quote">&ldquo;</span>{{ r.reason }}</div>
        <div class="pass-foot">
          <button class="tgl on" type="button" data-remember><span class="tgl-tr"><span class="tgl-kn"></span></span>Remember</button>
          <div class="pass-dur">
            <button class="dur-btn" type="button" data-dur-btn :data-min="r.duration"><span data-dur-label>{{ durLabel(r.duration) }}</span><svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="6 9 12 15 18 9"/></svg></button>
          </div>
          <span class="pass-id">{{ r.reqId }}</span>
        </div>
        <span class="pass-stamp">{{ r.verdict === 'denied' ? 'Denied' : 'Allowed' }}</span>
      </article>

    </div>

    <footer class="pg-ft">
      <span class="pg-keys"><kbd>&#8984;</kbd><kbd>&#8629;</kbd> allow all</span>
      <button class="pg-btn ghost" id="pgDenyAll" type="button">Deny All</button>
      <button class="pg-btn ghost" id="pgDenyCur" type="button">Deny Current</button>
      <button class="pg-btn main" id="pgAllowAll" type="button">Allow All</button>
    </footer>
  </div>
</div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { permissions, riskSummary, type RiskLevel } from '../data/store'

const risk = computed(() => riskSummary())

function levelLabel(level: RiskLevel) {
  return level === 'critical' ? 'Critical risk' : level === 'high' ? 'High risk' : 'Low risk'
}

function durLabel(min: number) {
  return min === 60 ? '1 hour' : min === 1440 ? '1 day' : min + ' minutes'
}
</script>
