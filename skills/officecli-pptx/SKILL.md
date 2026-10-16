---
name: officecli-pptx
description: "Use this skill any time a .pptx file is involved. Create slide decks, presentations; read/extract text; edit existing presentations; work with shapes, charts, animations, speaker notes."
---

# OfficeCLI PPTX Skill

## Setup

Follow install instructions from `officecli` skill.

## ⚠️ Help-First Rule

```bash
officecli help pptx
officecli help pptx shape
officecli help pptx add chart
```

## Requirements for Outputs

| Element | Minimum | Typical |
|---------|---------|---------|
| Slide title | ≥ 36pt bold | 36-44pt |
| Section/subtitle | ≥ 20pt | 20-24pt |
| Body text | ≥ 18pt | 18-22pt |
| Caption/axis | ≥ 10pt | 10-12pt |

- **Title ≥ 2× body size**. Left-align body; center only titles and hero numbers.
- **Two fonts max, one palette**: One heading font + one body font. 60-70% primary color, 1-2 supporting, one accent.
- **Every slide carries a non-text visual**: Shape, chart, icon, gradient band.
- **Speaker notes on every content slide**: `--type notes --prop text="..."`.
- **No placeholder tokens**: `{{name}}`, `<TODO>`, `xxxx`, `lorem`.
- **No overflow**: Every shape satisfies `x + width ≤ 33.87cm` AND `y + height ≤ 19.05cm`.
- **No text overflow inside shapes**: Enlarge the box or shorten text.

## Common Workflow

1. `officecli create "$FILE"` + `officecli open "$FILE"`
2. Build in display order: cover → agenda → content → closing
3. `officecli close "$FILE"` + `officecli validate "$FILE"`

### Key operations
```bash
# Slides and backgrounds
officecli add "$FILE" / --type slide --prop layout=blank --prop background=1E2761

# Shapes
officecli add "$FILE" /slide[2] --type shape --prop name=Title --prop text="Key Insight" \
  --prop x=2cm --prop y=2cm --prop width=20cm --prop height=3cm \
  --prop font=Georgia --prop size=36 --prop bold=true --prop color=1E2761

# Charts
officecli add "$FILE" /slide[3] --type chart --prop chartType=column \
  --prop series1.name=Revenue --prop series1.values="42,45,48" --prop series1.color=1E2761 \
  --prop categories="Q1,Q2,Q3" \
  --prop x=2cm --prop y=4cm --prop width=20cm --prop height=10cm

# Pictures
officecli add "$FILE" /slide[4] --type picture --prop src=hero.jpg \
  --prop x=1cm --prop y=1cm --prop width=32cm --prop height=18cm

# Speaker notes
officecli add "$FILE" "/slide[2]" --type notes --prop text="Lead with the key insight."

# Animations (1 per slide, ≤ 600ms)
officecli set "$FILE" "/slide[2]/shape[@name=HeroCard]" --prop animation=fade-entrance-400

# Connectors (always add arrowhead)
officecli add "$FILE" /slide[5] --type connector \
  --prop "from=/slide[5]/shape[@name=BoxA]" --prop "to=/slide[5]/shape[@name=BoxB]" \
  --prop shape=elbow --prop tailEnd=triangle
```

## Design Principles

Widescreen is **33.87 × 19.05cm**. Edge margin ≥ 1.27cm, inter-block gap ≥ 0.76cm, ≥ 20% negative space.

| Chart Type | When to use |
|-----------|-------------|
| column | Category comparison |
| line | Time series |
| pie/doughnut | Part-of-whole (2-5 slices) |
| scatter | Correlation/distribution |

## QA Delivery Gate

```bash
officecli validate "$FILE" && echo "Gate 1 OK" || { echo "REJECT"; exit 1; }
ISSUES=$(officecli view "$FILE" issues 2>&1 | grep -vE "no title")
echo "$ISSUES" | grep -qE "\[E[0-9]+\]" && { echo "REJECT"; exit 1; } || echo "Gate 2 OK"
# Run visual check: officecli view "$FILE" html
```
