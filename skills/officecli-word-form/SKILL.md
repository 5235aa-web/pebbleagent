---
name: officecli-word-form
description: "Create fillable Word forms with Content Controls (SDT), FormField checkboxes, MERGEFIELD mail-merge placeholders, and document protection."
---

# OfficeCLI Word Form Skill

**This skill is independent of officecli-docx.** A form uses SDT controls, legacy FormFields, and document protection.

## Setup

Follow install instructions from `officecli` skill.

## Three Paths

| Need | Path |
|------|------|
| Text/richtext SDT | A — Pure CLI (4 canonical props) |
| Dropdown with options | B — CLI + raw-set to inject items |
| Picture SDT, grouped SDT | C — Word template skeleton |

## Key Operations

```bash
# SDT text field
officecli add "$FILE" /body --type sdt --prop type=text --prop alias="Full Name" --prop tag=full_name --prop text="Enter name"

# SDT dropdown (Path A skeleton)
officecli add "$FILE" /body --type sdt --prop type=dropdown --prop alias="Department" --prop tag=dept
# Path B — inject items
officecli raw-set "$FILE" /document \
  --xpath "//w:sdt[w:sdtPr/w:tag/@w:val='dept']/w:sdtPr/w:dropDownList" \
  --action append \
  --xml '<w:listItem xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" w:displayText="Engineering" w:value="Engineering"/>'

# SDT date with format
officecli add "$FILE" /body --type sdt --prop type=date --prop alias="Start Date" --prop tag=start_date
officecli raw-set "$FILE" /document \
  --xpath "//w:sdt[w:sdtPr/w:tag/@w:val='start_date']/w:sdtPr/w:date/w:dateFormat" \
  --action setattr --xml "w:val=yyyy-MM-dd"

# Checkbox (use FormField, NOT SDT)
officecli add "$FILE" /body --type formfield --prop type=checkbox --prop name=agree_terms --prop checked=false

# MERGEFIELD
officecli add "$FILE" '/body/p[1]' --type field --prop fieldType=mergefield --prop name=CustomerName

# Lock SDT
officecli set "$FILE" '/body/sdt[1]' --prop lock=sdtlocked

# Document protection (LAST command)
officecli set "$FILE" / --prop protection=forms
```

## Requirements
- Every SDT has both `alias` + `tag`
- Checkbox uses FormField, NOT SDT
- FormField `name` ≤ 20 characters
- No underscore-line placeholders
- Zero `WARNING: UNSUPPORTED` in build log
