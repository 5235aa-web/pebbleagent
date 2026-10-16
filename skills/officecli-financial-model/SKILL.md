---
name: officecli-financial-model
description: "Build financial models in Excel — 3-statement model, DCF valuation, LBO, SaaS unit economics, sensitivity/scenario analysis. Scene layer on officecli-xlsx."
---

# OfficeCLI Financial Model Skill

**This skill is a scene layer on `officecli-xlsx`.** Load `officecli-xlsx` first for base rules.

## Setup

Follow install instructions from `officecli` skill.

## Three-Zone Architecture

| Zone | Sheet names | Tab color | Content |
|------|------------|-----------|---------|
| Inputs | Assumptions, Drivers | Yellow | Raw drivers, blue font |
| Calc | P&L, BS, CF, DCF | Blue | All formulas, zero hardcodes |
| Outputs | Summary, Sensitivity | Green | KPIs, charts, returns |

## Three Model Recipes

### A — 3-Statement Model (P&L + BS + CF)
Build order: Assumptions → P&L → BS → CF → Summary. Balance check: `IF(ABS(Assets-Liab-Equity)<0.01,"OK","IMBALANCED")`

### B — DCF Valuation
Build order: Assumptions → FCF → WACC → DCF → Sensitivity. Use `SUMPRODUCT` instead of `NPV` for correct caching.

### C — LBO Model
Build order: Assumptions → S&U → P&L → Debt → CF → Exit → Returns. Enable `calc.iterate` for circular refs.

## Key Rules
- **Blue font = inputs, Black = formulas, Green = cross-sheet, Red = external links**
- **Assumptions in cells, never inside formulas**: `=B5*(1+GrowthRate)`, not `=B5*1.05`
- **Guard every `/`**: `IFERROR(x/y, 0)`
- **Named ranges for ≥3-use drivers**: `WACC`, `TaxRate`, `TerminalGrowth`
- **BS.Cash = CF ending cash**: No independent plug
