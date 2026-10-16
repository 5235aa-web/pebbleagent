---
name: officecli-xlsx
description: "Use this skill any time a .xlsx file is involved. Create spreadsheets, financial models, dashboards, trackers; read/extract data; edit workbooks; work with formulas, charts, pivot tables; import CSV/TSV."
---

# OfficeCLI XLSX Skill

## Setup

Follow install instructions from `officecli` skill.

## ⚠️ Help-First Rule

```bash
officecli help xlsx
officecli help xlsx cell
officecli help xlsx chart
```

## Requirements for Outputs

- **Zero formula errors**: No `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, `#N/A`. Guard every `/` with IFERROR.
- **Formulas, not hardcoded values**: If a number can be computed, it is a formula.
- **Professional font**: Arial/Calibri/Times New Roman.
- **Explicit widths**: Labels 20-25, numbers 12-15, dates 12, short codes 8-10.
- **No `###` in any cell** — widen columns.
- **No placeholder tokens**: `$fy$24`, `{var}`, `<TODO>`, `xxxx`.

### Financial models only
- Blue text (`0000FF`) = hardcoded inputs
- Black (`000000`) = formulas
- Green (`008000`) = cross-sheet links
- Red (`FF0000`) = external file links
- Yellow fill = key assumptions

## Common Workflow

1. `officecli create "$FILE"` + `officecli open "$FILE"`
2. Build incrementally: structure → data → formulas → formatting
3. `officecli close "$FILE"` + `officecli validate "$FILE"`

### Key operations
```bash
# Cells and formulas
officecli set "$FILE" /Sheet1/A1 --prop value=Month --prop bold=true
officecli set "$FILE" /Sheet1/B5 --prop formula="SUM(B2:B4)" --prop numFmt='$#,##0'

# Named ranges (use batch heredoc for $ and !)
cat <<'EOF' | officecli batch "$FILE"
[{"command":"add","parent":"/","type":"namedrange","props":{"name":"GrowthRate","ref":"Sheet1!$B$6"}}]
EOF

# Charts
officecli add "$FILE" /Sheet1 --type chart --prop chartType=column \
  --prop dataRange='Sheet1!A1:B4' --prop title='Revenue'

# Pivot tables
officecli add "$FILE" /Sheet1 --type pivottable \
  --prop source="Sheet1!A1:E100" --prop rows=Region,Category \
  --prop cols=Year --prop values="Sales:sum"

# Sheets and formatting
officecli add "$FILE" / --type sheet --prop name=Summary --prop tabColor=70AD47
officecli set "$FILE" "/Sheet1/col[A]" --prop width=20
officecli set "$FILE" "/Sheet1" --prop freeze=A2

# CSV import
officecli import "$FILE" /Sheet1 --file data.csv --header

# Data validation
officecli add "$FILE" /Sheet1 --type validation \
  --prop sqref="C2:C100" --prop type=list \
  --prop formula1="Yes,No,Maybe"
```

## Cross-sheet formula tip
Use batch heredoc with `<<'EOF'` to avoid `!` shell mangling:
```bash
cat <<'EOF' | officecli batch "$FILE"
[{"command":"set","path":"/Summary/B2","props":{"formula":"Revenue!B13"}}]
EOF
```

## QA

```bash
officecli query "$FILE" 'cell:contains("#REF!")'
officecli query "$FILE" 'cell:contains("#DIV/0!")'
officecli validate "$FILE"
officecli view "$FILE" html  # visual check
```
