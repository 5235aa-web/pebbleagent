<template>
<div class="overlay" id="sheet-model">
  <div class="sheet">
    <div class="sheet-hdr">
      <span class="sheet-ico">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="4" width="16" height="16" rx="3"/><rect x="9" y="9" width="6" height="6" rx="1"/><line x1="9" y1="2" x2="9" y2="4"/><line x1="15" y1="2" x2="15" y2="4"/></svg>
      </span>
      <div class="sheet-title">Model &amp; agents<small>runtime configuration</small></div>
      <button class="sheet-x" data-close>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="sheet-body">
      <div class="section-h">agent</div>
      <div class="agents-strip">
        <div class="agent-card on">
          <div class="ac-name">code_agent
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
          <div class="ac-desc">Reads, edits and verifies code across the workspace.</div>
          <span class="ac-tag">46 tools</span>
        </div>
        <div class="agent-card">
          <div class="ac-name">research_agent</div>
          <div class="ac-desc">Searches docs, reads pages, cites sources.</div>
          <span class="ac-tag">12 tools</span>
        </div>
        <div class="agent-card">
          <div class="ac-name">ops_agent</div>
          <div class="ac-desc">Runs shell workflows and watches long jobs.</div>
          <span class="ac-tag">18 tools</span>
        </div>
      </div>

      <div class="section-h">provider</div>
      <div class="prov-tabs">
        <button class="prov-tab on"><span class="pt-dot"></span>Anthropic</button>
        <button class="prov-tab"><span class="pt-dot"></span>OpenAI</button>
        <button class="prov-tab"><span class="pt-dot"></span>DeepSeek</button>
        <button class="prov-tab"><span class="pt-dot"></span>Google</button>
      </div>

      <div class="field">
        <div class="field-lbl"><span>api key</span><span class="mono-tag">stored locally</span></div>
        <div class="field-in">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--faint)" stroke-width="1.8"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>
          <input type="password" value="sk-ant-api03-xxxxxxxxxxxxxxxxxxxx" />
          <span class="mono-tag">••••</span>
        </div>
      </div>

      <div class="section-h">models</div>
      <div class="model-list">
        <div class="model-row on">
          <span class="model-radio"></span>
          <span class="model-name">claude-sonnet-4.5</span>
          <span class="model-badge">vision</span>
          <span class="model-meta">200k ctx</span>
        </div>
        <div class="model-row">
          <span class="model-radio"></span>
          <span class="model-name">claude-opus-4.1</span>
          <span class="model-meta">200k ctx</span>
        </div>
        <div class="model-row">
          <span class="model-radio"></span>
          <span class="model-name">claude-haiku-4</span>
          <span class="model-badge">fast</span>
          <span class="model-meta">200k ctx</span>
        </div>
      </div>

      <div class="section-h">reasoning</div>
      <div class="detail-row" style="padding:0">
        <span class="detail-lbl">mode</span>
        <div class="seg">
          <button class="on">Auto</button>
          <button>Deep</button>
          <button>Off</button>
        </div>
      </div>
    </div>
    <div class="sheet-foot">
      <span class="sf-hint">changes apply to the next turn</span>
      <button class="btn btn-ghost" data-close>Cancel</button>
      <button class="btn btn-primary" data-close data-toast="Model switched to claude-sonnet-4.5">Apply</button>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
</script>
