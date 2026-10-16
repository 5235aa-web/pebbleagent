// test_time_groups.mjs — Sidebar conversation time-bucketing (bucketOf), EXECUTED FROM REAL SOURCE.
// The function is extracted from Sidebar.vue and run directly, so any drift in the
// component fails this test loudly. Extraction is strict on purpose.
// Fixed reference dates: 2026-10-15=Thu, 10-12=Mon, 11-01=Sun, 2026-01-02=Fri.
// Run: node tests/test_time_groups.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'

const vue = readFileSync(new URL('../ui-gui-vue3/src/components/Sidebar.vue', import.meta.url), 'utf8')

const m = vue.match(/function bucketOf\(ts: number \| undefined, now: Date\): string \{([\s\S]*?)\n\}/)
if (!m) throw new Error('cannot extract bucketOf from Sidebar.vue — update the extractor')
const bucketOf = new Function('ts', 'now', m[1])

let PASS = 0, FAIL = 0
function check(name, cond, detail = '') {
  if (cond) { PASS++; console.log(`  [PASS] ${name}`) }
  else { FAIL++; console.log(`  [FAIL] ${name} ${detail}`) }
}

const S = (y, mo, d, h = 12, mi = 0) => Math.floor(new Date(y, mo - 1, d, h, mi).getTime() / 1000)

// ---- Scenario 1: now = 2026-10-15 (Thu) 14:30 ----
console.log('=== Scenario 1: now=2026-10-15(Thu) ===')
const now1 = new Date(2026, 9, 15, 14, 30)
check('weekday check (Thu=4)', now1.getDay() === 4, `getDay=${now1.getDay()}`)
check('10-15 10:00 -> today', bucketOf(S(2026,10,15,10), now1) === 'today')
check('10-15 00:00 boundary -> today', bucketOf(S(2026,10,15,0,0), now1) === 'today')
check('10-14 23:59 -> yesterday', bucketOf(S(2026,10,14,23,59), now1) === 'yesterday')
check('10-14 00:00 boundary -> yesterday', bucketOf(S(2026,10,14,0,0), now1) === 'yesterday')
check('10-13 12:00 (Tue) -> this week', bucketOf(S(2026,10,13,12), now1) === 'this week')
check('10-12 00:00 (Mon) boundary -> this week', bucketOf(S(2026,10,12,0,0), now1) === 'this week')
check('10-11 23:59 (Sun) -> this month', bucketOf(S(2026,10,11,23,59), now1) === 'this month')
check('10-01 00:00 boundary -> this month', bucketOf(S(2026,10,1,0,0), now1) === 'this month')
check('09-30 23:59 -> last month', bucketOf(S(2026,9,30,23,59), now1) === 'last month')
check('09-01 00:00 boundary -> last month', bucketOf(S(2026,9,1,0,0), now1) === 'last month')
check('08-31 23:59 -> earlier', bucketOf(S(2026,8,31,23,59), now1) === 'earlier')
check('01-01 00:00 boundary -> earlier', bucketOf(S(2026,1,1,0,0), now1) === 'earlier')
check('2025-12-31 23:59 -> last year', bucketOf(S(2025,12,31,23,59), now1) === 'last year')
check('2024-06-01 -> last year', bucketOf(S(2024,6,1), now1) === 'last year')
check('undefined ts -> last year (fallback)', bucketOf(undefined, now1) === 'last year')

// ---- Scenario 2: now = 2026-10-12 (Mon) 10:00 ----
console.log('=== Scenario 2: now=2026-10-12(Mon) ===')
const now2 = new Date(2026, 9, 12, 10, 0)
check('weekday check (Mon=1)', now2.getDay() === 1, `getDay=${now2.getDay()}`)
check('mon: 10-12 09:00 -> today', bucketOf(S(2026,10,12,9), now2) === 'today')
check('mon: 10-11 20:00 (Sun) -> yesterday', bucketOf(S(2026,10,11,20), now2) === 'yesterday')
check('mon: 10-10 (Sat) -> this month (not in current week)', bucketOf(S(2026,10,10,10), now2) === 'this month')
check('mon: 10-05 -> this month', bucketOf(S(2026,10,5,10), now2) === 'this month')
check('mon: 09-30 -> last month', bucketOf(S(2026,9,30,10), now2) === 'last month')

// ---- Scenario 3: now = 2026-01-02 (Fri) 10:00 (year boundary) ----
console.log('=== Scenario 3: now=2026-01-02(Fri) year boundary ===')
const now3 = new Date(2026, 0, 2, 10, 0)
check('weekday check (Fri=5)', now3.getDay() === 5, `getDay=${now3.getDay()}`)
check('jan2: 01-01 -> yesterday', bucketOf(S(2026,1,1,10), now3) === 'yesterday')
check('jan2: 2025-12-31 -> this week (cross-year week, Mon=12-29)', bucketOf(S(2025,12,31,10), now3) === 'this week')
check('jan2: 2025-12-28 -> last month (Sun, before week start)', bucketOf(S(2025,12,28,10), now3) === 'last month')
check('jan2: 2025-12-01 boundary -> last month', bucketOf(S(2025,12,1,0,0), now3) === 'last month')
check('jan2: 2025-11-30 -> last year', bucketOf(S(2025,11,30,10), now3) === 'last year')

// ---- Scenario 4: now = 2026-11-01 (Sun) month/week crossover ----
console.log('=== Scenario 4: now=2026-11-01(Sun) crossover ===')
const now4 = new Date(2026, 10, 1, 10, 0)
check('weekday check (Sun=0)', now4.getDay() === 0, `getDay=${now4.getDay()}`)
check('nov1: 10-31 (Sat) -> yesterday', bucketOf(S(2026,10,31,10), now4) === 'yesterday')
check('nov1: 10-30 -> this week (week started 10-26)', bucketOf(S(2026,10,30,10), now4) === 'this week')
check('nov1: 10-26 (Mon) boundary -> this week', bucketOf(S(2026,10,26,0,0), now4) === 'this week')
check('nov1: 10-25 (Sun prev week) -> last month', bucketOf(S(2026,10,25,10), now4) === 'last month')
check('nov1: 10-01 boundary -> last month', bucketOf(S(2026,10,1,0,0), now4) === 'last month')
check('nov1: 09-30 -> earlier', bucketOf(S(2026,9,30,10), now4) === 'earlier')
check('nov1: 2025-12-31 -> last year', bucketOf(S(2025,12,31,10), now4) === 'last year')

console.log(`\n=== RESULT: PASS=${PASS} FAIL=${FAIL} ===`)
process.exit(FAIL === 0 ? 0 : 1)
