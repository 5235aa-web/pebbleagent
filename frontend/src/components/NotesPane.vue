<template>
        <div class="dr-pane" data-pane="notes" data-live="1">
          <div class="notes-block">
            <div class="nb-head">
              <span class="nb-kicker">01</span>
              <div class="nb-title">Quick notes<small>scratchpad · this session only</small></div>
              <span class="nb-count">{{ notes.quick.length }}</span>
            </div>
            <div class="note-list">
              <div class="note-row" v-for="(n, i) in notes.quick" :key="i"
                   role="button" tabindex="0"
                   @click="inject(n.text)"
                   @keydown.enter.prevent="inject(n.text)"
                   @keydown.space.prevent="inject(n.text)">
                <span class="nr-time">{{ n.time }}</span>
                <div class="nr-body">{{ n.text }}</div>
                <button class="nr-x" @click.stop="removeQuickNote(i)">
                  <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
              </div>
            </div>
            <div class="note-new">
              <textarea rows="2" v-model="draft" placeholder="Write a note for this session…" @keydown.meta.enter.prevent="save" @keydown.ctrl.enter.prevent="save"></textarea>
              <div class="note-new-foot">
                <span class="nnf-hint">⌘⏎ to save</span>
                <button class="btn btn-primary btn-sm" @click="save">Save</button>
              </div>
            </div>
          </div>

          <div class="notes-block">
            <div class="nb-head">
              <span class="nb-kicker">02</span>
              <div class="nb-title">User note<small>prepended to every turn</small></div>
              <span class="badge-soft">long-term</span>
            </div>
            <div class="standing-card">
              <div class="standing-text" v-if="!editing">{{ notes.usernote || 'No user note yet.' }}</div>
              <textarea v-else class="lm-ta" rows="6" v-model="usernoteDraft"></textarea>
              <div class="standing-foot">
                <span class="badge-soft">user scope</span>
                <button class="btn btn-ghost btn-sm" @click="toggleEdit">{{ editing ? 'Save' : 'Edit' }}</button>
              </div>
            </div>
          </div>
        </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { notes } from '../data/panels'
import { addQuickNote, appendToComposer, removeQuickNote, saveUsernote } from '../data/wire'

const draft = ref('')
const editing = ref(false)
const usernoteDraft = ref('')

/** A note is a fragment, not a message: clicking one drops it into the composer
    and the reader decides when it is ready to send. */
function inject(text: string): void {
  appendToComposer(text)
}

function save() {
  const body = draft.value.trim()
  if (!body) return
  addQuickNote(body)
  draft.value = ''
}

/** One button for both states: it opens the editor, then writes it back. */
function toggleEdit() {
  if (!editing.value) {
    usernoteDraft.value = notes.usernote
    editing.value = true
    return
  }
  saveUsernote(usernoteDraft.value.trim())
  editing.value = false
}
</script>
