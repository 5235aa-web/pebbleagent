// test_fold_algo.mjs — compact-segment fold algorithm, EXECUTED FROM REAL SOURCE.
// The three computeds (segments / segIndex / renderItems) are extracted from
// VirtualMessageList.vue and run directly, so any algorithm drift in the component
// fails this test loudly. Extraction is intentionally strict: if the source shape
// changes, the extractor throws and the test must be updated.
// Run: node tests/test_fold_algo.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'

const vue = readFileSync(new URL('../ui-gui-vue3/src/components/VirtualMessageList.vue', import.meta.url), 'utf8')

let PASS = 0, FAIL = 0
function check(name, cond, detail = '') {
  if (cond) { PASS++; console.log(`  [PASS] ${name}`) }
  else { FAIL++; console.log(`  [FAIL] ${name} ${detail}`) }
}

// ==================== Source extraction ====================
function extract(re, label) {
  const m = vue.match(re)
  if (!m) throw new Error(`cannot extract ${label} from VirtualMessageList.vue — update the extractor`)
  return m[1]
}
function convert(src, label, replacements) {
  let out = src
  for (const [from, to] of replacements) {
    if (!out.includes(from)) throw new Error(`${label}: source pattern missing (drift detected): ${from}`)
    out = out.split(from).join(to)
  }
  return out
}

const segBody = convert(
  extract(/const segments = computed<Segment\[\]>\(\(\) => \{([\s\S]*?)\n\}\)/, 'segments'),
  'segments',
  [
    ['const arr = g.messages', 'const arr = __arr'],
    ['const anchors: number[] = []', 'const anchors = []'],
    ['const segs: Segment[] = []', 'const segs = []'],
  ],
)
const computeSegments = new Function('__arr', segBody)

const idxBody = convert(
  extract(/const segIndex = computed\(\(\) => \{([\s\S]*?)\n\}\)/, 'segIndex'),
  'segIndex',
  [
    ['const byMsg = new Map<number, Segment>()', 'const byMsg = new Map()'],
    ['const byAnchor = new Map<number, Segment>()', 'const byAnchor = new Map()'],
    ['const segOf: (Segment | null)[] = new Array(g.messages.length).fill(null)', 'const segOf = new Array(__arrLen).fill(null)'],
    ['segments.value', '__segments'],
  ],
)
const computeSegIndex = new Function('__segments', '__arrLen', idxBody)

const renBody = convert(
  extract(/const renderItems = computed<RenderItem\[\]>\(\(\) => \{([\s\S]*?)\n\}\)/, 'renderItems'),
  'renderItems',
  [
    ['const arr = all.value', 'const arr = __arr'],
    ['const { byAnchor, segOf } = segIndex.value', 'const { byAnchor, segOf } = __segIndex'],
    ['const out: RenderItem[] = []', 'const out = []'],
    ['expanded.value', '__expanded'],
  ],
)
const computeRenderItems = new Function('__arr', '__segIndex', '__expanded', renBody)

// ==================== Synthetic cases ====================
function m(id, opts = {}) { return { id, role: 'agent', content: '', ...opts } }
const boundary = (id) => m('cb_' + id, { isCompactBoundary: true })
const compacted = (id) => m('hist_' + id, { isCompacted: true })

console.log('=== Case 1: typical multi-compaction structure ===')
{
  const arr = [
    ...Array.from({ length: 5 }, (_, i) => compacted(i)),
    boundary('b1'),
    m('s1', { isCompacted: true }),
    ...Array.from({ length: 3 }, (_, i) => compacted(10 + i)),
    boundary('b2'),
    m('s2'),
    ...Array.from({ length: 4 }, (_, i) => m(20 + i)),
  ]
  const segs = computeSegments(arr)
  const idx = computeSegIndex(segs, arr.length)
  check('2 segments', segs.length === 2, `got ${segs.length}`)
  check('seg1 = [0,5) collapsible', segs[0].start === 0 && segs[0].end === 5 && segs[0].collapsible)
  check('seg2 = [6,10) collapsible (incl s1)', segs[1].start === 6 && segs[1].end === 10 && segs[1].collapsible)
  const collapsed = computeRenderItems(arr, idx, new Set())
  check('collapsed items = 7 (2 dividers + s2 + 4 active)', collapsed.length === 7, `got ${collapsed.length}`)
  check('dividers count 5 and 4', collapsed[0].kind === 'divider' && collapsed[0].count === 5 && collapsed[1].kind === 'divider' && collapsed[1].count === 4)
  const exp1 = computeRenderItems(arr, idx, new Set(['cb_b1']))
  check('expand b1 -> 12 items (5 msg + 2 divider + s2 + 4 active)', exp1.length === 12, `got ${exp1.length}`)
  const expAll = computeRenderItems(arr, idx, new Set(['cb_b1', 'cb_b2']))
  check('expand all -> 16 items (all elements)', expAll.length === 16, `got ${expAll.length}`)
  check('jump idx 3 -> seg b1', idx.byMsg.get(3)?.bid === 'cb_b1')
  check('jump idx 7 -> seg b2', idx.byMsg.get(7)?.bid === 'cb_b2')
  check('jump idx 12 -> no seg (active)', idx.byMsg.get(12) === undefined)
}

console.log('=== Case 2: consecutive anchors (empty segment) ===')
{
  const arr = [boundary('b1'), boundary('b2'), m('a1')]
  const segs = computeSegments(arr)
  check('2 segs, both not collapsible', segs.length === 2 && !segs[0].collapsible && !segs[1].collapsible)
  const items = computeRenderItems(arr, computeSegIndex(segs, arr.length), new Set())
  check('no dividers, only 1 msg', items.length === 1 && items[0].kind === 'msg', `got ${items.length}`)
}

console.log('=== Case 3: mixed segment (not all compacted) ===')
{
  const arr = [compacted(0), m('a1'), boundary('b1'), m('a2')]
  const segs = computeSegments(arr)
  check('seg not collapsible', segs.length === 1 && !segs[0].collapsible)
  const items = computeRenderItems(arr, computeSegIndex(segs, arr.length), new Set())
  check('all 3 msgs rendered, no divider', items.length === 3 && items.every(i => i.kind === 'msg'), `got ${items.length}`)
}

console.log('=== Case 4: no anchors at all ===')
{
  const arr = [m('a1'), m('a2'), m('a3')]
  const segs = computeSegments(arr)
  const items = computeRenderItems(arr, computeSegIndex(segs, arr.length), new Set())
  check('no segments, all rendered', segs.length === 0 && items.length === 3)
}

console.log('=== Case 5: large synthetic session (scale + perf) ===')
{
  // Deterministic synthetic data, same shape as a real multi-compaction history:
  // [compacted block] [boundary] x N + trailing active block.
  function synthSessionData(nMsgs, nBoundaries) {
    const arr = []
    const activeCount = Math.max(2, Math.floor(nMsgs * 0.15))
    const histCount = nMsgs - activeCount
    const perSeg = Math.floor(histCount / nBoundaries)
    let id = 0
    for (let b = 0; b < nBoundaries; b++) {
      const cnt = (b === nBoundaries - 1) ? (histCount - perSeg * (nBoundaries - 1)) : perSeg
      for (let i = 0; i < cnt; i++) {
        id++
        arr.push({ id: 'h' + id, role: i % 2 ? 'agent' : 'user', content: 'history message ' + id, isCompacted: true })
      }
      arr.push({ id: 'cb' + b, role: 'system', content: '[COMPACT_BOUNDARY:' + b + ']', isCompactBoundary: true })
    }
    for (let i = 0; i < activeCount; i++) {
      id++
      arr.push({ id: 'a' + id, role: i % 2 ? 'agent' : 'user', content: 'active message ' + id })
    }
    return arr
  }

  const arr = synthSessionData(2000, 5)
  const nB = arr.filter(x => x.isCompactBoundary).length
  const nC = arr.filter(x => x.isCompacted).length
  console.log(`  synthetic ${arr.length} msgs (boundaries=${nB} compacted=${nC})`)
  const t0 = Date.now()
  const segs = computeSegments(arr)
  const idx = computeSegIndex(segs, arr.length)
  const collapsed = computeRenderItems(arr, idx, new Set())
  const expandedAll = computeRenderItems(arr, idx, new Set(segs.map(s => s.bid)))
  const ms = Date.now() - t0
  check('segments == boundaries', segs.length === nB, `${segs.length} vs ${nB}`)
  check('all segments collapsible', segs.every(s => s.collapsible))
  const foldable = segs.reduce((a, s) => a + (s.end - s.start), 0)
  const oldMarkedB = arr.filter(x => x.isCompactBoundary && x.isCompacted).length
  check('foldable == compacted - old marked boundaries', foldable === nC - oldMarkedB, `got ${foldable}, expect ${nC - oldMarkedB} (nC=${nC}, oldMarkedB=${oldMarkedB})`)
  check('collapsed items == total - foldable', collapsed.length === arr.length - foldable, `got ${collapsed.length}, expect ${arr.length - foldable}`)
  check('collapsed has N dividers', collapsed.filter(i => i.kind === 'divider').length === nB)
  check('expanded items == total', expandedAll.length === arr.length, `got ${expandedAll.length}`)
  console.log(`  collapsed renders ${collapsed.length} items (was ${arr.length}) -${(100 - Math.round(collapsed.length / arr.length * 100))}%`)
  console.log(`  algorithm time: ${ms}ms for ${arr.length} msgs`)
  check('perf < 50ms', ms < 50, `${ms}ms`)
}

console.log(`\n=== RESULT: PASS=${PASS} FAIL=${FAIL} ===`)
process.exit(FAIL === 0 ? 0 : 1)
