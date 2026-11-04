#!/usr/bin/env node
/* Provider-card UI helpers.

   The request-shaping block under the `opencode-go` divider is specific to
   OpenCode. Showing it for OpenRouter, DeepSeek or a blank new card invites
   users to edit knobs that do nothing. The predicate lives outside the Vue
   component so this rule stays testable without mounting the pane.

   Run: node --experimental-transform-types --import ./tests/ts-register.mjs \
          tests/test_provider_ui.mjs      (exit 0 = pass)
*/
import assert from 'node:assert/strict'
import { isOpenCodeProvider, isProviderReady } from '../frontend/src/data/panels.ts'

let passed = 0
const failures = []

function check(label, fn) {
  try {
    fn()
    passed++
    console.log(`[PASS] ${label}`)
  } catch (err) {
    failures.push(`${label}: ${err.message.split('\n')[0]}`)
    console.log(`[FAIL] ${label}: ${err.message.split('\n')[0]}`)
  }
}

check('exact opencode name shows the block', () => {
  assert.equal(isOpenCodeProvider('opencode'), true)
})
check('display-name casing does not matter', () => {
  assert.equal(isOpenCodeProvider('OpenCode'), true)
})
check('OpenCode Go shows the block', () => {
  assert.equal(isOpenCodeProvider('OpenCode Go'), true)
})
check('hyphenated opencode-go shows the block', () => {
  assert.equal(isOpenCodeProvider('opencode-go'), true)
})
check('underscore spelling shows the block', () => {
  assert.equal(isOpenCodeProvider('OPENCODE_GO'), true)
})
check('openrouter hides the block', () => {
  assert.equal(isOpenCodeProvider('openrouter'), false)
})
check('deepseek hides the block', () => {
  assert.equal(isOpenCodeProvider('deepseek'), false)
})
check('a blank new card hides the block', () => {
  assert.equal(isOpenCodeProvider('New provider'), false)
})
check('a name that merely contains opencode is not the gateway', () => {
  assert.equal(isOpenCodeProvider('my-opencode'), false)
})

check('a provider needs both a base url and a model', () => {
  assert.equal(isProviderReady({ baseUrl: 'https://example.com/v1', models: ['m'] }), true)
  assert.equal(isProviderReady({ baseUrl: '', models: ['m'] }), false)
  assert.equal(isProviderReady({ baseUrl: 'https://example.com/v1', models: [] }), false)
  assert.equal(isProviderReady({ baseUrl: '   ', models: ['m'] }), false)
  assert.equal(isProviderReady(null), false)
})

console.log()
if (failures.length) {
  console.log(`FAILED: ${failures.length} / ${passed + failures.length}`)
  process.exit(1)
}
console.log(`ALL PASSED: ${passed} checks`)
