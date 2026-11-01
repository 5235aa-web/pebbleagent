#!/usr/bin/env node
/* Block splitting and the live block builder.

   splitBlocks decides what a settled answer turns into: markdown runs, fenced
   code, mermaid diagrams, SVG graphics. LiveBuilder decides what a streaming
   answer turns into at each delta, and its two invariants are easy to break:
   a fence must not be committed before it closes, and the block array must be
   mutated in place because the row component watches that identity.

   Run: node --experimental-transform-types --import ./tests/ts-register.mjs \
          tests/test_blocks_split.mjs      (exit 0 = pass)
*/
import assert from 'node:assert/strict'
import { splitBlocks, LiveBuilder } from '../frontend/src/data/blocks.ts'

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

/* ---- splitBlocks ---- */

check('splitBlocks turns plain text into one markdown block', () => {
  const blocks = splitBlocks('hello', false, 'b')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].kind, 'text')
  assert.equal(blocks[0].id, 'b-t0')
  assert.equal(blocks[0].html, '<p>hello</p>')
})

check('splitBlocks returns nothing for empty text', () => {
  /* The trailing-text rule keeps one block so an empty answer still renders. */
  const blocks = splitBlocks('', false, 'b')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].kind, 'text')
  assert.equal(blocks[0].html, '')
})

check('splitBlocks turns a closed fence into a code block', () => {
  const blocks = splitBlocks('```js\nconst x = 1\n```', false, 'b')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].kind, 'code')
  assert.equal(blocks[0].lang, 'js')
  assert.equal(blocks[0].code, 'const x = 1\n')
  assert.equal(blocks[0].lines, 1)
  assert.equal(blocks[0].streaming, false)
})

check('splitBlocks marks an unclosed fence as still streaming', () => {
  const blocks = splitBlocks('```js\nx', false, 'b')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].kind, 'code')
  assert.equal(blocks[0].streaming, true)
})

check('splitBlocks names an unlabelled fence "code"', () => {
  const blocks = splitBlocks('```\nx\n```', false, 'b')
  assert.equal(blocks[0].kind, 'code')
  assert.equal(blocks[0].lang, 'code')
})

check('splitBlocks routes a mermaid fence to its own block', () => {
  const blocks = splitBlocks('```mermaid\ngraph TD\n```', false, 'b')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].kind, 'mermaid')
  assert.equal(blocks[0].id, 'b-m0')
  assert.equal(blocks[0].code, 'graph TD\n')
  assert.equal(blocks[0].streaming, false)
})

check('splitBlocks routes an svg fence to its own block', () => {
  const blocks = splitBlocks('```svg\n<svg/>\n```', false, 'b')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].kind, 'svg')
  assert.equal(blocks[0].id, 'b-s0')
})

check('splitBlocks matches the fence language case-insensitively', () => {
  assert.equal(splitBlocks('```Mermaid\ngraph TD\n```', false, 'b')[0].kind, 'mermaid')
  assert.equal(splitBlocks('```SVG\n<svg/>\n```', false, 'b')[0].kind, 'svg')
})

check('splitBlocks keeps the markdown run before a fence', () => {
  const blocks = splitBlocks('a\n\n```js\nx\n```', false, 'b')
  assert.equal(blocks.length, 2)
  assert.equal(blocks[0].kind, 'text')
  assert.equal(blocks[0].id, 'b-t0')
  assert.equal(blocks[0].html, '<p>a</p>')
  assert.equal(blocks[1].kind, 'code')
  assert.equal(blocks[1].id, 'b-c1')
})

check('splitBlocks keeps the markdown run after a fence', () => {
  const blocks = splitBlocks('```js\nx\n```\nafter', false, 'b')
  assert.equal(blocks.length, 2)
  assert.equal(blocks[0].kind, 'code')
  assert.equal(blocks[1].kind, 'text')
  assert.equal(blocks[1].id, 'b-t1')
  assert.equal(blocks[1].html, '<p>after</p>')
})

check('splitBlocks numbers every block from one counter', () => {
  const blocks = splitBlocks('a\n\n```js\nx\n```\n\nb', false, 'z')
  const ids = blocks.map(b => b.id)
  assert.deepEqual(ids, ['z-t0', 'z-c1', 'z-t2'])
})

check('splitBlocks drops an empty tail after a fence', () => {
  assert.equal(splitBlocks('```js\nx\n```', false, 'b').length, 1)
})

check('splitBlocks renders a streaming answer through the caret path', () => {
  const blocks = splitBlocks('still going', true, 'b')
  assert.ok(blocks[0].html.includes('<span class="caret"></span>'))
})

/* ---- LiveBuilder ---- */

check('LiveBuilder commits a finished paragraph before the open tail', () => {
  const lb = new LiveBuilder('r')
  const blocks = lb.push('a\n\n')
  assert.equal(blocks.length, 2)
  assert.equal(blocks[0].kind, 'text')
  assert.equal(blocks[0].id, 'r-m0-t0')
  assert.equal(blocks[0].html, '<p>a</p>')
  assert.equal(blocks[1].kind, 'raw')
  assert.equal(blocks[1].id, 'r-raw')
})

check('LiveBuilder keeps the open tail in one raw block', () => {
  const lb = new LiveBuilder('r')
  lb.push('a\n\n')
  const blocks = lb.push('b')
  assert.equal(blocks.length, 2)
  assert.equal(blocks[1].kind, 'raw')
  assert.deepEqual(blocks[1].paras, ['b'])
})

check('LiveBuilder mutates the raw tail in place instead of replacing it', () => {
  const lb = new LiveBuilder('r')
  lb.push('a\n\n')
  const rawBefore = lb.blocks[1]
  lb.push('b')
  assert.equal(lb.blocks[1], rawBefore)
})

check('LiveBuilder returns the same array instance on every push', () => {
  const lb = new LiveBuilder('r')
  const first = lb.push('a')
  const second = lb.push('\nb')
  assert.equal(first, second)
  assert.equal(first, lb.blocks)
})

check('LiveBuilder holds a fence open until it closes', () => {
  const lb = new LiveBuilder('r')
  const blocks = lb.push('```js\nx\n\n')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].kind, 'raw')
  assert.equal(blocks.filter(b => b.kind === 'code').length, 0)
})

check('LiveBuilder commits a closed fence as a code block', () => {
  const lb = new LiveBuilder('r')
  lb.push('```js\nx\n```\n\n')
  const code = lb.blocks.filter(b => b.kind === 'code')
  assert.equal(code.length, 1)
  assert.equal(code[0].code, 'x\n')
})

check('LiveBuilder finish replaces everything with the settled blocks', () => {
  const lb = new LiveBuilder('r')
  const live = lb.push('a\n\nb')
  lb.finish('a\n\nb')
  assert.equal(live, lb.blocks)
  assert.equal(lb.blocks.length, 1)
  assert.equal(lb.blocks[0].kind, 'text')
  assert.equal(lb.blocks[0].html, '<p>a</p><p>b</p>')
})

check('LiveBuilder finish splits fences from the full answer', () => {
  const lb = new LiveBuilder('r')
  lb.finish('a\n\n```js\nx\n```')
  assert.deepEqual(lb.blocks.map(b => b.kind), ['text', 'code'])
})

check('LiveBuilder reset empties the same array instance', () => {
  const lb = new LiveBuilder('r')
  lb.push('a\n\nb')
  const live = lb.blocks
  lb.reset()
  assert.equal(live, lb.blocks)
  assert.equal(lb.blocks.length, 0)
})

check('LiveBuilder restarts its block counter after reset', () => {
  const lb = new LiveBuilder('r')
  lb.push('a\n\n')
  lb.reset()
  lb.push('b\n\n')
  assert.equal(lb.blocks[0].id, 'r-m0-t0')
})

console.log(`\nRESULT: PASS=${passed} FAIL=${failures.length}`)
if (failures.length) {
  for (const f of failures) console.log(`  - ${f}`)
  process.exit(1)
}
