<template>
<div class="overlay" id="newEntryDialog">
  <div class="sheet ne">
    <div class="ne-hdr">
      <div>
        <div class="ne-title">New Experience</div>
        <div class="ne-sub">record a lesson that outlives this session</div>
      </div>
      <button class="sheet-x" id="neClose" @click="close">&#215;</button>
    </div>
    <div class="ne-body">
      <div class="ne-field">
        <label>Kind</label>
        <div class="ne-kinds" id="neKinds">
          <button
            v-for="k in KINDS"
            :key="k.key"
            class="ne-kind"
            :class="[k.cls, { on: kind === k.key }]"
            @click="kind = k.key"
          >{{ k.label }}</button>
        </div>
      </div>

      <div class="ne-field">
        <label>Scope</label>
        <div class="ne-seg" id="neScope">
          <button :class="{ on: scope === 'project' }" @click="scope = 'project'">Project</button>
          <button :class="{ on: scope === 'global' }" @click="scope = 'global'">Global</button>
        </div>
      </div>
      <div class="ne-field">
        <label>Statement</label>
        <textarea class="ne-ta" rows="2" placeholder="one-sentence conclusion..." v-model="statement"></textarea>
      </div>
      <div class="ne-field">
        <label>Rationale</label>
        <textarea class="ne-ta" rows="2" placeholder="why it matters, or how it failed..." v-model="rationale"></textarea>
      </div>

      <div class="ne-field">
        <label>Tools</label>
        <input class="ne-input" placeholder="comma separated, e.g. write_file, edit_file" v-model="tools" />
      </div>
      <div class="ne-field">
        <label>Confidence <b id="neConfVal">{{ confidence.toFixed(2) }}</b></label>
        <input class="ne-range" id="neConf" type="range" min="0" max="1" step="0.05" v-model.number="confidence" />
      </div>
    </div>
    <div class="ne-foot">
      <button class="ne-cancel" id="neCancel" @click="close">Cancel</button>
      <button class="ne-create" id="neCreate" @click="create">Create</button>
    </div>
  </div>
</div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { createMemory, toast } from '../data/wire'

const KINDS = [
  { key: 'correction', label: 'Correction', cls: 'corr' },
  { key: 'constraint', label: 'Constraint', cls: 'cons' },
  { key: 'preference', label: 'Preference', cls: 'pref' },
  { key: 'pitfall', label: 'Pitfall', cls: 'pit' },
  { key: 'recipe', label: 'Recipe', cls: 'reci' },
  { key: 'reference', label: 'Reference', cls: 'ref' }
]

const kind = ref('correction')
const scope = ref('project')
const statement = ref('')
const rationale = ref('')
const tools = ref('')
const confidence = ref(0.8)

async function create() {
  const ok = await createMemory({
    kind: kind.value,
    statement: statement.value,
    rationale: rationale.value,
    scope: scope.value,
    tools: tools.value.split(',').map(s => s.trim()).filter(Boolean),
    confidence: confidence.value
  })
  if (!ok) {
    /* The sheet stays open with the text intact; saying why beats leaving the
       user clicking Save at a gateway that already refused. */
    toast('Could not add entry', 'The gateway refused the new entry.', 'err')
    return
  }
  toast('Entry added', 'The experience ledger now carries it.', 'ok')
  close()
}

/** Closes the sheet through the same class the overlay system uses, then resets
    the form so the next open starts blank. */
function close() {
  const el = document.getElementById('newEntryDialog')
  el?.classList.remove('open')
  el?.setAttribute('aria-hidden', 'true')
  statement.value = ''
  rationale.value = ''
  tools.value = ''
  confidence.value = 0.8
  kind.value = 'correction'
  scope.value = 'project'
}
</script>
