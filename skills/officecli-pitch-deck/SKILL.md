---
name: officecli-pitch-deck
description: "Build fundraising/investor pitch decks — seed, Series A/B/C, convertible note, SAFE round, strategic raise. Scene layer on officecli-pptx."
---

# OfficeCLI Pitch Deck Skill

**This skill is a scene layer on `officecli-pptx`.** Load `officecli-pptx` first for base rules.

## Setup

Follow install instructions from `officecli` skill.

## Stage Diagnosis

| Stage | Revenue | Slides | Must-have data |
|-------|---------|--------|---------------|
| Seed | $0-$1M ARR | 10-12 | Founder-market fit, pilot logos |
| Series A | $1-$5M ARR | 12-16 | NRR > 110%, bottom-up TAM |
| Series B | $5-$30M ARR | 18-22 | ARR curve, NRR, CAC/LTV, payback |
| Series C | $30M+ ARR | 20-24 | Multi-year GAAP, rule-of-40, moat |

## 10 Essential Slides

1. **Cover** — company + tagline + round + date (dark gradient)
2. **Problem** — 3 data cards with sources
3. **Solution** — product pattern description + flow
4. **Market** — TAM/SAM/SOM nested
5. **Product** — screenshot + 3 feature bullets
6. **Model** — CAC/LTV/payback/GM (Series A+)
7. **Traction** — ARR curve starting at 0
8. **Team** — avatars + prior companies with exits
9. **Financials** — 4-year plan + assumptions
10. **Ask** — hero number + 4-bucket Use-of-Funds + runway

## Key Rules

- **Single-quote every shape text containing `$`**: `--prop text='$35M'`
- **ARR curve y-axis starts at 0**: `--prop axismin=0`
- **Every content slide has speaker notes**: `--type notes`
- **Team slide carries prior companies**: `ex-Datadog Director (IPO)`
- **Use-of-Funds has 4 buckets**: Engineering/GTM/G&A/Reserve
