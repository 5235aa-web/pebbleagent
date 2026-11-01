<template>
<div class="overlay" id="sheet-pairing">
  <div class="sheet" style="max-width:600px">
    <div class="sheet-hdr">
      <span class="sheet-ico">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>
      </span>
      <div class="sheet-title">Pair a device<small>mobile companion · TLS pinned</small></div>
      <button class="sheet-x" data-close>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="sheet-body">
      <div class="pair-wrap">
        <div class="qr" id="qrGrid">
          <i class="on accent"></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i class="on"></i><i class="on accent"></i>
          <i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i class="on"></i>
          <i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i>
          <i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i></i>
          <i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i>
          <i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i class="on"></i><i></i>
          <i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i>
          <i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i>
          <i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i>
          <i class="on"></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i class="on"></i><i class="on accent"></i>
          <i class="on accent"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i></i><i class="on"></i><i class="on"></i><i class="on"></i><i class="on"></i><i class="on accent"></i>
        </div>
        <div class="pair-info">
          <div class="pair-code">{{ codeText }}</div>
          <div class="pair-meta">
            <div><span class="k">address</span> &nbsp;{{ addressText }}</div>
            <div><span class="k">expires</span> &nbsp;single use · rotates per pairing</div>
            <div><span class="k">clients</span> &nbsp;{{ pairing.enabled ? 'listener online' : 'listener off' }}</div>
          </div>
          <div class="fingerprint">SHA-256 {{ pairing.fingerprint || 'not issued' }}</div>
        </div>
      </div>

      <div class="section-h">transport</div>
      <div class="detail-row" style="padding:0">
        <span class="detail-lbl">tls listener</span>
        <div class="seg">
          <button :class="{ on: pairing.enabled }" @click="setPairingEnabled(true)">Enabled</button>
          <button :class="{ on: !pairing.enabled }" @click="setPairingEnabled(false)">Disabled</button>
        </div>
      </div>
    </div>
    <div class="sheet-foot">
      <span class="sf-hint">code rotates on every pairing</span>
      <button class="btn btn-ghost" data-close>Close</button>
      <button class="btn btn-primary" @click="mintPairingCode()">New code</button>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { pairing } from '../data/panels'
import { mintPairingCode, refreshPairing, setPairingEnabled } from '../data/wire'

/* The gateway hands back a compact code; the sheet prints it spaced out so it
   can be read off a screen at arm's length. */
const codeText = computed(() => {
  const raw = pairing.code.replace(/\s+/g, '').toUpperCase()
  if (!raw) return '---- · ----'
  const half = Math.ceil(raw.length / 2)
  return (raw.slice(0, half) + ' · ' + raw.slice(half)).split('').join(' ')
})

const addressText = computed(() => {
  if (!pairing.host || !pairing.port) return 'no LAN address'
  return 'wss://' + pairing.host + ':' + pairing.port
})

onMounted(() => {
  void refreshPairing()
})
</script>
