<template>
        <div class="dr-pane" data-pane="notes" data-live="1">
          <div class="notes-block">
            <div class="nb-head">
              <span class="nb-kicker">01</span>
              <div class="nb-title">Quick notes<small>click to insert into your message</small></div>
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
              <div class="nb-title">User note<small>prepended to every turn · will send to the LLM</small></div>
              <span class="badge-soft">long-term</span>
            </div>
            <div class="standing-card">
              <div class="standing-text" v-if="!editing">{{ notes.usernote || 'No user note yet.' }}</div>
              <textarea v-else class="nb-ta" rows="6" v-model="usernoteDraft"></textarea>
              <div class="standing-foot">
                <span class="badge-soft">user scope</span>
                <button class="btn btn-ghost btn-sm" @click="toggleEdit">{{ editing ? 'Save' : 'Edit' }}</button>
              </div>
            </div>
          </div>

          <div class="notes-block">
            <div class="nb-head">
              <span class="nb-kicker">03</span>
              <div class="nb-title">Personal notebook<small>notes to yourself · the LLM never sees them</small></div>
              <button class="pnb-add" @click="addThought" aria-label="Add thought">
                <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                add thought
              </button>
            </div>
            <div class="pnb-rail" v-if="notes.notebook.length">
              <article v-for="e in notes.notebook" :key="e.id" class="pnb-card"
                       :class="{ 'is-editing': editingId === e.id }">
                <div class="pnb-meta">
                  <time class="pnb-time">{{ e.time }}</time>
                  <div class="pnb-tools" v-if="editingId !== e.id">
                    <button class="pnb-ic" @click="startEdit(e)" aria-label="Edit entry">
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
                    </button>
                    <button class="pnb-ic pnb-del" @click="removeNotebookEntry(e.id)" aria-label="Delete entry">
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                  </div>
                </div>
                <p class="pnb-text" v-if="editingId !== e.id">{{ e.text }}</p>
                <template v-else>
                  <textarea id="pnbEditor" class="pnb-ta" rows="4" v-model="editDraft"
                            placeholder="Write it down while it is still fresh…"
                            @keydown.meta.enter.prevent="commitEdit"
                            @keydown.ctrl.enter.prevent="commitEdit"
                            @keydown.esc.prevent="cancelEdit"></textarea>
                  <div class="pnb-actions">
                    <button class="pnb-act" @click="cancelEdit">Cancel</button>
                    <button class="pnb-act primary" @click="commitEdit">Save</button>
                  </div>
                </template>
              </article>
            </div>
            <div class="pnb-empty" v-else>
              <span class="pnb-empty-mark">no entries</span>
              <p>Thoughts kept here stay on this machine and are never sent to a model.</p>
            </div>
          </div>
        </div>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { notes, type NotebookEntry } from '../data/panels'
import {
  addNotebookEntry, addQuickNote, appendToComposer, removeNotebookEntry,
  removeQuickNote, saveNotebookEntry, saveUsernote
} from '../data/wire'

const draft = ref('')
const editing = ref(false)
const usernoteDraft = ref('')

const editingId = ref('')
const editDraft = ref('')

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

/* ---- personal notebook ---- */

/** Only one entry is ever open, so the editor is found by id: a template `ref`
    inside `v-for` would collect an array instead of the single textarea. */
function focusEditor(): void {
  void nextTick(() => {
    const ta = document.getElementById('pnbEditor') as HTMLTextAreaElement | null
    if (!ta) return
    ta.focus()
    ta.setSelectionRange(ta.value.length, ta.value.length)
  })
}

function addThought(): void {
  editingId.value = addNotebookEntry()
  editDraft.value = ''
  focusEditor()
}

function startEdit(entry: NotebookEntry): void {
  editingId.value = entry.id
  editDraft.value = entry.text
  focusEditor()
}

function commitEdit(): void {
  const id = editingId.value
  if (!id) return
  saveNotebookEntry(id, editDraft.value)
  editingId.value = ''
  editDraft.value = ''
}

/** Cancel keeps whatever the file already holds; only a never-saved draft goes. */
function cancelEdit(): void {
  const id = editingId.value
  if (!id) return
  const entry = notes.notebook.find(e => e.id === id)
  if (entry && !entry.text) removeNotebookEntry(id)
  editingId.value = ''
  editDraft.value = ''
}
</script>
