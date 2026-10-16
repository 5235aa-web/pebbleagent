// test_input_resize.mjs — input-box drag resize: clamp formula boundary tests + component source consistency.
// Formula mirror of ui-gui-vue3/src/components/InputArea.vue (drift guard).
// Run: node tests/test_input_resize.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'

const MIN_H = 38

function clampH(startH, delta, maxHVal) {
  return Math.max(MIN_H, Math.min(startH + delta, maxHVal))
}
function maxHCalc(winH, mainH) {
  const m = mainH ?? winH
  return Math.max(MIN_H, Math.min(Math.floor(winH * 0.7), m - 180))
}

let pass = 0, fail = 0
function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`[PASS] ${name}`) }
  else { fail++; console.log(`[FAIL] ${name} ${detail}`) }
}

// ===== 1. maxH calculation =====
check('maxH: 1080 win / 1012 main -> 756', maxHCalc(1080, 1012) === 756, String(maxHCalc(1080, 1012)))
check('maxH: 600 win / 532 main -> 352 (main-180 constraint)', maxHCalc(600, 532) === 352, String(maxHCalc(600, 532)))
check('maxH: tiny window fallback -> MIN_H', maxHCalc(300, 120) === MIN_H, String(maxHCalc(300, 120)))
check('maxH: main missing -> viewport 70%', maxHCalc(1000, null) === 700, String(maxHCalc(1000, null)))

// ===== 2. drag clamp =====
const MAX1 = maxHCalc(1080, 1012) // 756
check('default 38 drag up 300 -> 338', clampH(38, 300, MAX1) === 338)
check('default 38 drag up 1000 -> clamp to 756', clampH(38, 1000, MAX1) === 756)
check('300 drag up 1000 -> clamp to 756', clampH(300, 1000, MAX1) === 756)
check('drag down beyond (300-500) -> 38', clampH(300, -500, MAX1) === 38)
check('300 drag down 100 -> 200', clampH(300, -100, MAX1) === 200)
check('300 drag down 262 -> 38', clampH(300, -262, MAX1) === 38)

// ===== 3. reset decision (<= MIN_H+1 -> null) =====
const isReset = h => h !== null && h <= MIN_H + 1
check('38 -> reset', isReset(38))
check('39 -> reset (1px tolerance)', isReset(39))
check('40 -> no reset', !isReset(40))
check('null -> no reset', !isReset(null))

// ===== 4. persist decision (> MIN_H+1 only) =====
const shouldPersist = h => h !== null && h > MIN_H + 1
check('38 not persisted', !shouldPersist(38))
check('39 not persisted', !shouldPersist(39))
check('40 persisted', shouldPersist(40))
check('338 persisted', shouldPersist(338))
check('null not persisted', !shouldPersist(null))

// ===== 5. window shrink protection =====
const onWinClamp = (h, mx) => (h !== null && h > mx ? mx : h)
const MAX2 = maxHCalc(600, 532) // 352
check('756 meets 600 window -> 352', onWinClamp(756, MAX2) === 352)
check('200 meets 600 window -> 200 (untouched)', onWinClamp(200, MAX2) === 200)
check('null meets small window -> null', onWinClamp(null, MAX2) === null)

// ===== 6. persisted value restore =====
const useSaved = s => !!s && s > MIN_H + 1
check('restore 900 (1080 win) -> min(900,756)=756', Math.min(900, MAX1) === 756)
check('restore 900 (600 win) -> min(900,352)=352', Math.min(900, MAX2) === 352)
check('restore 38 -> ignored', !useSaved(38))
check('restore 39 -> ignored', !useSaved(39))
check('restore 40 -> adopted', useSaved(40))

// ===== 7. component source consistency (formula drift guard) =====
const src = readFileSync(new URL('../ui-gui-vue3/src/components/InputArea.vue', import.meta.url), 'utf8')
check('source has MIN_H = 38', /const MIN_H = 38/.test(src))
check('source clamp formula matches', src.includes('Math.max(MIN_H, Math.min(dragStartH + delta, maxH()))'))
check('source maxH has 0.7 and 180', /0\.7/.test(src) && /- 180/.test(src))
check('source reset rule <= MIN_H + 1', src.includes('<= MIN_H + 1'))
check('source persist key pebble:input-height', src.includes("'pebble:input-height'"))
check('source has ia-resizing global class', src.includes("'ia-resizing'"))
check('source has setPointerCapture', src.includes('setPointerCapture'))

console.log(`\nRESULT: ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
