<template>
<div class="overlay cmd-overlay" id="cmdOverlay">
  <div class="cmd">
    <div class="cmd-in">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7.5"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>
      <input id="cmdInput" type="text" placeholder="Jump to a session, run a command, open a panel…" />
      <span class="cmd-kbd">ESC</span>
    </div>
    <div class="cmd-list" id="cmdList">
      <div class="cmd-group-lbl">actions</div>
      <div class="cmd-item sel">
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">New session</span><span class="cmd-d">start a clean transcript</span></span>
        <span class="cmd-k">⌘N</span>
      </div>
      <div class="cmd-item" data-sess>
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="14" y2="18"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">Switch session</span><span class="cmd-d">5 sessions · pinned first</span></span>
        <span class="cmd-k">⌘F</span>
      </div>
      <div class="cmd-item">
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">Compact context</span><span class="cmd-d">62.1k tokens in play</span></span>
        <span class="cmd-k">⌘⇧C</span>
      </div>
      <div class="cmd-item" data-open="sheet-theme">
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">Switch appearance</span><span class="cmd-d">daylight / nightfall · accent</span></span>
        <span class="cmd-k">⌘⇧L</span>
      </div>
      <div class="cmd-item" data-todo>
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">Toggle task list</span><span class="cmd-d">left panel · live plan</span></span>
      </div>
      <div class="cmd-group-lbl">panels</div>
      <div class="cmd-item" data-stage>
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2.5" y="4" width="19" height="13" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">Open preview stage</span><span class="cmd-d">usage-card.html · 1440 × 900</span></span>
      </div>
      <div class="cmd-item" data-sheet="msPop">
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><line x1="20" y1="20" x2="16" y2="16"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">Search messages</span><span class="cmd-d">full text in this session</span></span>
      </div>
      <div class="cmd-item" data-open-tab="notes">
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16v12l-4 4H4z"/><line x1="8" y1="9" x2="16" y2="9"/><line x1="8" y1="13" x2="13" y2="13"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">Notes &amp; standing note</span><span class="cmd-d">scratchpad, long-term rules</span></span>
      </div>
      <div class="cmd-item" data-open="sheet-model">
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="3"/><rect x="9" y="9" width="6" height="6" rx="1"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">Model &amp; agents</span><span class="cmd-d">provider, key, model</span></span>
      </div>
      <div class="cmd-item" data-open="sheet-ledger">
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">Experience ledger</span><span class="cmd-d">14 entries</span></span>
      </div>
      <div class="cmd-item" data-open="sheet-mcp">
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="19" r="2"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">MCP servers</span><span class="cmd-d">3 installed · 2 running</span></span>
      </div>
      <div class="cmd-item" data-open="sheet-pairing">
        <span class="cmd-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="2"/></svg></span>
        <span class="cmd-main"><span class="cmd-t">Pair a device</span><span class="cmd-d">QR · single-use code</span></span>
      </div>
      <div class="cmd-group-lbl">sessions</div>
      <div class="cmd-item">
        <span class="cmd-ico" style="background:var(--accent-soft);color:var(--accent)">07</span>
        <span class="cmd-main"><span class="cmd-t">Context compaction budget</span><span class="cmd-d">31 msgs · 3h ago</span></span>
      </div>
      <div class="cmd-item">
        <span class="cmd-ico" style="background:var(--accent-soft);color:var(--accent)">06</span>
        <span class="cmd-main"><span class="cmd-t">Android catch-up resync after sleep</span><span class="cmd-d">24 msgs · 5h ago</span></span>
      </div>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
</script>
