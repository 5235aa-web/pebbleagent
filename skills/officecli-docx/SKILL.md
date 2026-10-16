---
name: officecli-docx
description: "Use this skill any time a .docx file is involved. Create Word documents, reports, letters, memos; read/extract text; edit/update existing documents; work with templates, tracked changes, comments, headers/footers, or tables of contents."
---

# OfficeCLI DOCX Skill

## Setup

Follow install instructions from `officecli` skill.

## ⚠️ Help-First Rule

**When uncertain about property names or syntax, run help before guessing:**
```bash
officecli help docx
officecli help docx paragraph
officecli help docx set paragraph
```

## Requirements for Outputs

### All documents
- **Clear hierarchy**: Title → Heading1 → Heading2 → body. Use explicit heading sizes: H1 = 18-20pt, H2 = 14pt bold, H3 = 12pt bold italic.
- **One body font, one accent**: Calibri, Cambria, Georgia, or Times New Roman.
- **Spacing through properties**: Use `spaceBefore`/`spaceAfter`, not empty paragraphs.
- **Headers, footers, page numbers** on documents > 1 page. Use `--prop field=page` for live page numbers.
- **No placeholder tokens**: `{{name}}`, `<TODO>`, `xxxx`, `lorem` must not appear in output.

### Visual delivery floor
Run `officecli view "$FILE" html` and check:
- No placeholder tokens rendered as data
- No truncated titles or overflowing cells
- Page numbers render as live fields (not static text)
- TOC present when document has 3+ headings

## Common Workflow

1. `officecli create "$FILE"` + `officecli open "$FILE"`
2. Build incrementally: structure → content → formatting
3. `officecli close "$FILE"` + `officecli validate "$FILE"`
4. QA: `view issues`, `view outline`, `query` for defects

### Key operations
```bash
# Paragraphs
officecli add "$FILE" /body --type paragraph --prop text="Title" --prop style=Heading1 --prop size=18pt --prop bold=true

# Tables
officecli add "$FILE" /body --type table --prop rows=4 --prop cols=3 --prop width=100%
officecli set "$FILE" "/body/tbl[1]/tr[1]" --prop header=true --prop c1=Quarter --prop c2=Revenue

# Lists
officecli add "$FILE" /body --type paragraph --prop text="Item" --prop listStyle=bullet

# Footer with page number
officecli add "$FILE" / --type footer --prop type=default --prop align=center --prop size=9pt --prop text="Page " --prop field=page

# Images
officecli add "$FILE" "/body/p[5]" --type picture --prop src=chart.png --prop width=4in

# Table of Contents
officecli add "$FILE" /body --type toc --prop levels="1-3" --prop hyperlinks=true --index 0

# Page breaks
officecli add "$FILE" /body --type pagebreak
```

### Fields
```bash
officecli add "$FILE" "/body/p[3]" --type field --prop fieldType=mergefield --prop name=customer_name
officecli add "$FILE" "/body/p[3]" --type field --prop fieldType=page
```

## QA Delivery Gate

```bash
officecli close "$FILE"
officecli validate "$FILE" | grep -q "no errors found" || { echo "REJECT: validate failed"; exit 1; }
LEAK=$(officecli view "$FILE" text | grep -cE '(\$\w+\$|{{|TODO|xxxx|lorem|Update field to see|\\[t\n])')
[ "$LEAK" -eq 0 ] || { echo "REJECT: token leak"; exit 1; }
FLD=$(officecli query "$FILE" 'field[fieldType=page]' --json | jq '.data.results | length')
[ "$FLD" -ge 1 ] || { echo "REJECT: no live PAGE field"; exit 1; }
echo "Gate PASS"
```
