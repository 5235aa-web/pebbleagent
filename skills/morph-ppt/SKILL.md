---
name: morph-ppt
description: "Build PowerPoint decks with Morph transitions — smooth cross-slide animation, Keynote-style continuous motion, shapes that grow/move/rotate across slides."
---

# OfficeCLI Morph PPT Skill

**This skill is a scene layer on `officecli-pptx`.** Load `officecli-pptx` first for base rules.

## Setup

Follow install instructions from `officecli` skill.

## Core Mechanics

PowerPoint's Morph transition creates smooth motion between adjacent slides by matching shapes with **identical names**.

### Three-Prefix Naming System

| Prefix | Role | Example |
|--------|------|---------|
| `!!scene-*` | Persistent decoration across deck | `!!scene-ring`, `!!scene-bg-band` |
| `!!actor-*` | Content that evolves, then exits | `!!actor-feature-box`, `!!actor-metric` |
| `#sN-*` | Per-slide content (ghost on next) | `#s1-title`, `#s2-kpi` |

### Key Rules
- **Same name on both slides** for morph pairing
- **Ghost off-canvas** (`x=36cm`) instead of deleting
- **Every `!!actor-*` must have a planned exit slide**
- **No shape name collision**: `!!scene-card` + `!!actor-card` in same deck = broken
- **Per-slide ghosting**: Each slide is independent — re-ghost actors on every new slide

## Key Operations

```bash
# Slide 1 — hero shape
officecli add "$FILE" / --type slide --prop layout=blank --prop background=1E2761
officecli add "$FILE" /slide[1] --type shape --prop 'name=!!actor-headline' \
  --prop text="The one idea" --prop x=4cm --prop y=8cm --prop width=26cm --prop height=3cm \
  --prop font=Georgia --prop size=48 --prop bold=true --prop color=FFFFFF

# Slide 2 — morph transition + same-name shape moves
officecli add "$FILE" / --type slide --prop layout=blank --prop background=1E2761 --prop transition=morph
officecli add "$FILE" /slide[2] --type shape --prop 'name=!!actor-headline' \
  --prop text="The one idea" --prop x=1.5cm --prop y=1cm --prop width=12cm --prop height=1.5cm \
  --prop font=Georgia --prop size=24 --prop bold=true --prop color=FFFFFF

# Ghost previous content on slide 2
officecli set "$FILE" "/slide[2]/shape[@name=#s1-title]" --prop x=36cm

# Slide 3 — ghost actor from slide 2
officecli add "$FILE" / --type slide --prop layout=blank --prop background=1E2761 --prop transition=morph
officecli set "$FILE" "/slide[3]/shape[@name=!!actor-headline]" --prop x=36cm
```

## Known Issues
- After setting `transition=morph`, CLI auto-prepends `!!` to shape names — use index paths `/slide[N]/shape[K]` instead of `@name=`
- Morph renders only in PowerPoint 365, Keynote, WPS
- Adjacent slides need ≥ 3 shapes varying by ≥ 5cm or ≥ 15° to avoid plain fade
