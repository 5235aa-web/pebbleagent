#!/usr/bin/env node
/* Transcript state: append, replace, clear.

   The transcript lives in one reactive singleton that every row component
   reads. Two properties matter and both have broken in production:
   `items` must keep its array identity so Vue patches rows instead of
   rebuilding them, and any rebuild must settle `streaming`, otherwise a resume
   that raced a session switch leaves the flag on and pins the composer to the
   stop button.

   Run: node --experimental-transform-types --import ./tests/ts-register.mjs \
          tests/test_stream_state.mjs      (exit 0 = pass)
*/
import assert from 'node:assert/strict'
import {
  streamState,
  replaceStream,
  appendStream,
  clearStream,
  streamRowEl
} from '../frontend/src/data/stream.ts'

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
  } finally {
    clearStream()
    streamState.sessionId = null
    streamState.model = ''
    streamState.gatewayOnline = false
  }
}

function row(id, kind = 'message') {
  return { id, kind, className: '', attrs: {}, innerHTML: '' }
}

/* ---- initial shape (must run first: the module is a singleton) ---- */

check('streamState starts empty and offline', () => {
  assert.deepEqual(streamState.items, [])
  assert.equal(streamState.streaming, false)
  assert.equal(streamState.sessionId, null)
  assert.equal(streamState.model, '')
  assert.equal(streamState.gatewayOnline, false)
})

/* ---- append ---- */

check('appendStream adds one row at the end', () => {
  appendStream(row('a'))
  appendStream(row('b'))
  assert.deepEqual(streamState.items.map(i => i.id), ['a', 'b'])
})

check('appendStream keeps the array identity', () => {
  const items = streamState.items
  appendStream(row('a'))
  assert.equal(streamState.items, items)
})

check('appendStream accepts a compacted row with its collapse state', () => {
  appendStream({
    id: 'c1',
    kind: 'compact',
    className: '',
    attrs: {},
    innerHTML: '',
    compact: { bid: 'b1', count: 7, tools: 3, collapsed: true }
  })
  assert.equal(streamState.items[0].compact.collapsed, true)
  assert.equal(streamState.items[0].compact.count, 7)
})

/* ---- replace ---- */

check('replaceStream swaps the whole transcript', () => {
  appendStream(row('old'))
  replaceStream([row('n1'), row('n2')])
  assert.deepEqual(streamState.items.map(i => i.id), ['n1', 'n2'])
})

check('replaceStream keeps the array identity', () => {
  const items = streamState.items
  replaceStream([row('n1')])
  assert.equal(streamState.items, items)
})

check('replaceStream settles a live row', () => {
  streamState.streaming = true
  replaceStream([row('n1')])
  assert.equal(streamState.streaming, false)
})

check('replaceStream accepts an empty transcript', () => {
  appendStream(row('a'))
  replaceStream([])
  assert.deepEqual(streamState.items, [])
  assert.equal(streamState.streaming, false)
})

check('replaceStream leaves session metadata alone', () => {
  streamState.sessionId = 's1'
  streamState.model = 'm1'
  streamState.gatewayOnline = true
  replaceStream([row('n1')])
  assert.equal(streamState.sessionId, 's1')
  assert.equal(streamState.model, 'm1')
  assert.equal(streamState.gatewayOnline, true)
})

/* ---- clear ---- */

check('clearStream empties the transcript', () => {
  appendStream(row('a'))
  appendStream(row('b'))
  clearStream()
  assert.deepEqual(streamState.items, [])
})

check('clearStream keeps the array identity', () => {
  const items = streamState.items
  appendStream(row('a'))
  clearStream()
  assert.equal(streamState.items, items)
})

check('clearStream settles a live row', () => {
  streamState.streaming = true
  clearStream()
  assert.equal(streamState.streaming, false)
})

/* ---- streamRowEl ---- */

function fakeEl(display, classes = [], children = []) {
  return {
    display,
    classList: { contains: name => classes.includes(name) },
    children,
    firstElementChild: children[0] ?? null
  }
}

function withFakeDom(innerChildren, fn) {
  const prevDoc = globalThis.document
  const prevWin = globalThis.window
  globalThis.document = {
    getElementById: id => (id === 'streamInner' ? { children: innerChildren } : null)
  }
  globalThis.window = {
    getComputedStyle: el => ({ display: el.display })
  }
  try {
    fn()
  } finally {
    globalThis.document = prevDoc
    globalThis.window = prevWin
  }
}

check('streamRowEl returns the row directly when it has a layout box', () => {
  const agentRow = fakeEl('flex', ['msg', 'agent'])
  withFakeDom([agentRow], () => {
    assert.equal(streamRowEl(0), agentRow)
  })
})

check('streamRowEl unwraps display:contents wrapper to the inner message box', () => {
  const bubble = fakeEl('flex', ['msg', 'user'])
  const wrap = fakeEl('contents', ['msg-row-wrap', 'is-user-row'], [bubble])
  withFakeDom([wrap], () => {
    assert.equal(streamRowEl(0), bubble)
  })
})

check('streamRowEl skips fwd-check and returns wrapper when selectable', () => {
  const checkBox = fakeEl('inline-grid', ['fwd-check'])
  const bubble = fakeEl('flex', ['msg', 'user'])
  const contentsWrap = fakeEl('contents', ['msg-row-wrap'], [checkBox, bubble])
  const flexWrap = fakeEl('flex', ['msg-row-wrap', 'is-selectable'], [checkBox, bubble])
  withFakeDom([contentsWrap, flexWrap], () => {
    assert.equal(streamRowEl(0), bubble)
    assert.equal(streamRowEl(1), flexWrap)
  })
})

check('streamRowEl returns null when streamInner or index is missing', () => {
  withFakeDom([], () => {
    assert.equal(streamRowEl(0), null)
  })
})

console.log(`\nRESULT: PASS=${passed} FAIL=${failures.length}`)
if (failures.length) {
  for (const f of failures) console.log(`  - ${f}`)
  process.exit(1)
}
