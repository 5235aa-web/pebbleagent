<template>
<div class="overlay" id="sheet-theme">
  <div class="sheet" style="max-width:520px">
    <div class="sheet-hdr">
      <span class="sheet-ico">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="4.2"/><line x1="12" y1="2" x2="12" y2="4"/><line x1="12" y1="20" x2="12" y2="22"/><line x1="2" y1="12" x2="4" y2="12"/><line x1="20" y1="12" x2="22" y2="12"/></svg>
      </span>
      <div class="sheet-title">Appearance<small>surface &amp; accent</small></div>
      <button class="sheet-x" data-close>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="sheet-body">
      <div class="theme-grid">
        <div class="theme-card on" data-mode="light" data-set-theme="light">
          <div class="theme-preview">
            <div class="tp-side"></div>
            <div class="tp-main">
              <div class="tp-line a"></div>
              <div class="tp-line b"></div>
              <div class="tp-line c"></div>
              <div class="tp-line b"></div>
            </div>
          </div>
          <div class="theme-name">Daylight
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
          <div class="theme-sub">warm paper · default</div>
        </div>
        <div class="theme-card" data-mode="dark" data-set-theme="dark">
          <div class="theme-preview">
            <div class="tp-side"></div>
            <div class="tp-main">
              <div class="tp-line a"></div>
              <div class="tp-line b"></div>
              <div class="tp-line c"></div>
              <div class="tp-line b"></div>
            </div>
          </div>
          <div class="theme-name">Nightfall
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
          <div class="theme-sub">deep ink · low glare</div>
        </div>
      </div>

      <div class="section-h">accent</div>
      <div class="swatches" id="swatches">
        <button class="swatch on" data-accent="#D64E2A" style="background:#D64E2A"></button>
        <button class="swatch" data-accent="#1F7A6E" style="background:#1F7A6E"></button>
        <button class="swatch" data-accent="#A8792A" style="background:#A8792A"></button>
        <button class="swatch" data-accent="#2F5FA8" style="background:#2F5FA8"></button>
        <button class="swatch" data-accent="#7A3E9D" style="background:#7A3E9D"></button>
        <button class="swatch" data-accent="#3E6B2F" style="background:#3E6B2F"></button>
      </div>

    </div>
    <div class="sheet-foot">
      <span class="sf-hint">accent propagates to charts, badges and rings</span>
      <button class="btn btn-primary" data-close>Done</button>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
</script>
