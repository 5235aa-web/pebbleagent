<template>
<div class="overlay" id="sheet-notify">
  <div class="sheet nt">
    <div class="sheet-hdr">
      <span class="sheet-ico">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>
      </span>
      <div class="sheet-title">Notifications<small>audio &amp; desktop alerts</small></div>
      <button class="sheet-x" data-close>&#215;</button>
    </div>
    <div class="sheet-body">
      <div class="section-h">reply done</div>
      <div class="model-list">
        <div
          class="model-row"
          v-for="s in SOUNDS"
          :key="'done-' + s.id"
          :class="{ on: settings.soundDone === s.id }"
          @click="pick('done', s.id)"
        >
          <span class="model-radio"></span>
          <span class="model-name">{{ s.name }}</span>
          <span class="model-meta">{{ s.blurb }}</span>
        </div>
      </div>

      <div class="section-h">needs input</div>
      <div class="model-list">
        <div
          class="model-row"
          v-for="s in SOUNDS"
          :key="'ask-' + s.id"
          :class="{ on: settings.soundAsk === s.id }"
          @click="pick('ask', s.id)"
        >
          <span class="model-radio"></span>
          <span class="model-name">{{ s.name }}</span>
          <span class="model-meta">{{ s.blurb }}</span>
        </div>
      </div>

      <div class="section-h">volume</div>
      <div class="nt-vol">
        <input type="range" min="0" max="100" v-model.number="settings.volume" @change="preview" />
        <b>{{ settings.volume }}%</b>
      </div>

      <div class="section-h">alerts</div>
      <div class="nt-rows">
        <div class="nt-row">
          <span>Desktop notification</span>
          <button class="llm-tg" :class="{ on: settings.alerts.desktop }" @click="toggleAlert('desktop')"><i></i></button>
        </div>
        <div class="nt-row">
          <span>Permission requests</span>
          <button class="llm-tg" :class="{ on: settings.alerts.permission }" @click="toggleAlert('permission')"><i></i></button>
        </div>
        <div class="nt-row">
          <span>Task finished</span>
          <button class="llm-tg" :class="{ on: settings.alerts.finished }" @click="toggleAlert('finished')"><i></i></button>
        </div>
      </div>

      <div class="section-h">when</div>
      <div class="ne-seg nt-when">
        <button :class="{ on: settings.when === 'always' }" @click="settings.when = 'always'">Always</button>
        <button :class="{ on: settings.when === 'unfocused' }" @click="settings.when = 'unfocused'">Unfocused only</button>
      </div>
    </div>
    <div class="sheet-foot">
      <span class="sf-hint">one sound per finished turn · one when a question arrives</span>
      <button class="btn btn-ghost" @click="preview">Test</button>
      <button class="btn btn-primary" data-close>Done</button>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
import { notifySettings, SOUNDS, playSound, requestDesktopPermission, type SoundId } from '../data/notify'

const settings = notifySettings

/* Clicking a row selects it and plays it once, so the choice is made by ear
   rather than by the name of the timbre. */
function pick(slot: 'done' | 'ask', id: SoundId): void {
  if (slot === 'done') settings.soundDone = id
  else settings.soundAsk = id
  playSound(id)
}

function preview(): void {
  playSound(settings.soundDone)
}

function toggleAlert(key: 'desktop' | 'permission' | 'finished'): void {
  settings.alerts[key] = !settings.alerts[key]
  // Turning on desktop alerts is the gesture that asks for the browser grant.
  if (key === 'desktop' && settings.alerts.desktop) requestDesktopPermission()
}
</script>
