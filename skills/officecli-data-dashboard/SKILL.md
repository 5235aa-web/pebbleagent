---
name: officecli-data-dashboard
description: "Build multi-element Excel dashboards — KPI cards, charts, sparklines, conditional formatting — from CSV or tabular data. Scene layer on officecli-xlsx."
---

# OfficeCLI Data Dashboard Skill

**This skill is a scene layer on `officecli-xlsx`.** Load `officecli-xlsx` first for base rules.

## Setup

Follow install instructions from `officecli` skill.

## Core Principles

1. **Formula-driven KPIs**: Every KPI value is a formula, never hardcoded.
2. **Cell-range chart references**: `series1.values="Sheet1!B2:B13"`, not inline data.
3. **Dashboard-first**: User lands on Dashboard sheet. Raw data is upstream.
4. **visible cells only for chart sources**: Hidden columns render blank in some viewers.
5. **Scale with data size**: 200+ rows → 3-5 KPIs, 3 charts, CF rules.

## Key Operations

```bash
# Create and import
officecli create dashboard.xlsx
officecli import "$FILE" /Sheet1 --file sales.csv --header

# KPI card
officecli set "$FILE" /Dashboard/A1 --prop value="Total Revenue" --prop font.size=9 --prop font.color=666666
officecli set "$FILE" /Dashboard/A2 --prop formula==SUM(Sheet1!B2:B13) --prop numFmt='$#,##0' --prop font.size=24 --prop bold=true

# Sparkline
officecli add "$FILE" /Dashboard --type sparkline --prop cell=B2 --prop range='Sheet1!B2:B13' --prop type=line --prop color=4472C4

# Chart
officecli add "$FILE" /Dashboard --type chart --prop chartType=line --prop title="Revenue Trend" \
  --prop series1.name="Revenue" --prop series1.values='Sheet1!B2:B13' --prop x=0 --prop y=5 --prop width=10 --prop height=15

# Conditional formatting
officecli add "$FILE" /Data --type databar --prop sqref=B2:B13 --prop color=4472C4 --prop min=0 --prop max=100000

# Final setup
officecli set "$FILE" / --prop calc.fullCalcOnLoad=true
```
