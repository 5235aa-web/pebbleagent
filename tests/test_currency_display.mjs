#!/usr/bin/env node
/* Multi-currency display formatting and exchange-rate conversion tests. */
import assert from 'node:assert/strict'
import {
  usage,
  fmtCost,
  fmtMoney,
  setDisplayCurrency,
  setExchangeRate
} from '../frontend/src/data/panels.ts'

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

check('fmtCost keeps 4 decimals below 0.01 and 2 decimals at or above 0.01', () => {
  assert.equal(fmtCost(0, 'usd'), '$0.00')
  assert.equal(fmtCost(0.0014, 'usd'), '$0.0014')
  assert.equal(fmtCost(0.0072, 'cny'), '¥0.0072')
  assert.equal(fmtCost(1.25, 'usd'), '$1.25')
})

check('fmtMoney converts between CNY and USD using dynamic exchange rate', () => {
  setExchangeRate(7.0)
  usage.currency = 'cny'
  setDisplayCurrency('usd')
  assert.equal(fmtMoney(7.0, 'cny'), '$1.00')
  assert.equal(fmtMoney(0.01, 'cny'), '$0.0014')
  assert.equal(fmtMoney(2.0, 'usd'), '$2.00')

  setDisplayCurrency('cny')
  assert.equal(fmtMoney(2.0, 'usd'), '¥14.00')
  assert.equal(fmtMoney(7.0, 'cny'), '¥7.00')
})

check('setExchangeRate ignores non-positive or NaN inputs', () => {
  setExchangeRate(7.25)
  setExchangeRate(0)
  setExchangeRate(-3)
  setExchangeRate(Number.NaN)
  assert.equal(usage.cnyPerUsd, 7.25)
})

console.log()
if (failures.length) {
  console.log(`FAILED: ${failures.length} / ${passed + failures.length}`)
  process.exit(1)
}
console.log(`ALL PASSED: ${passed} checks`)
