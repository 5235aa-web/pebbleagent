---
name: officecli
description: Create, analyze, proofread, and modify Office documents (.docx, .xlsx, .pptx) using the officecli CLI tool. Use when the user wants to create, inspect, check formatting, find issues, add charts, or modify Office documents.
---

# officecli

AI-friendly CLI for .docx, .xlsx, .pptx. Single binary, no dependencies, no Office installation needed.

## Install

if officecli is missing:
```bash
# Windows (PowerShell)
irm https://d.officecli.ai/install.ps1 | iex
```

Verify with `officecli --version`.

**⚠️ PATH 注意：** 安装脚本更新用户 PATH 后，当前终端需手动设置 `$env:Path = "$env:LOCALAPPDATA\OfficeCLI;$env:Path"` 才能直接用 `officecli` 命令。也可以使用完整路径 `& "$env:LOCALAPPDATA\OfficeCLI\officecli.exe"`。

## Strategy

**L1 (read) → L2 (DOM edit) → L3 (raw XML)**. Always prefer higher layers. Add `--json` for structured output.

**Before doc work, check Specialized Skills.** Use `load_skill` to load format-specific skills: `officecli-docx`, `officecli-pptx`, `officecli-xlsx`, or deeper scene skills (pitch-deck, academic-paper, financial-model, etc.).

## Help System (IMPORTANT)

**When unsure about property names, value formats, or command syntax, ALWAYS run help instead of guessing.**

```bash
officecli help                                  # All commands
officecli help docx                             # List all docx elements
officecli help pptx                             # List all pptx elements
officecli help xlsx                             # List all xlsx elements
officecli help docx paragraph                   # Full schema
officecli help docx set paragraph               # Verb-filtered
officecli help docx paragraph --json            # Structured schema
```

## Performance: Resident Mode

Every command auto-starts a resident on first access (60s idle timeout). Explicit open/close recommended for longer sessions:

```bash
officecli open report.docx
officecli set report.docx ...
officecli close report.docx
```

## File-name convention

Use `$FILE` variable: `FILE="doc.docx"` then `officecli create "$FILE"`.

## Quick Start

**PPT:**
```bash
officecli create slides.pptx
officecli add slides.pptx / --type slide --prop title="Q4 Report" --prop background=1A1A2E
officecli add slides.pptx '/slide[1]' --type shape --prop text="Revenue grew 25%" --prop x=2cm --prop y=5cm --prop font=Arial --prop size=24 --prop color=FFFFFF
```

**Word:**
```bash
officecli create report.docx
officecli add report.docx /body --type paragraph --prop text="Executive Summary" --prop style=Heading1
officecli add report.docx /body --type paragraph --prop text="Revenue increased by 25%."
```

**Excel:**
```bash
officecli create data.xlsx
officecli set data.xlsx /Sheet1/A1 --prop value="Name" --prop bold=true
officecli set data.xlsx /Sheet1/A2 --prop value="Alice"
```

## L1: Create, Read & Inspect

```bash
officecli create <file>               # Create blank file
officecli view <file> <mode>          # outline | stats | issues | text | annotated | html
officecli get <file> <path> --depth N # Get a node [--json]
officecli query <file> <selector>     # CSS-like query
officecli validate <file>             # Validate against OpenXML schema
```

### view modes

| Mode | Description |
|------|-------------|
| `outline` | Document structure |
| `stats` | Statistics |
| `issues` | Formatting/content/structure problems |
| `text` | Plain text extraction |
| `annotated` | Text with formatting annotations |
| `html` | Static HTML snapshot — same renderer as watch |

### get

```bash
officecli get report.docx '/body/p[3]' --depth 2 --json
officecli get slides.pptx '/slide[1]' --depth 1
officecli get data.xlsx '/Sheet1/B2' --json
```

### query

CSS-like selectors: `[attr=value]`, `:contains("text")`, `:empty`.

```bash
officecli query report.docx 'paragraph[style=Normal]'
officecli query slides.pptx 'shape[fill=FF0000]'
```

## L2: DOM Operations

### set — modify properties

```bash
officecli set <file> <path> --prop key=value [--prop ...]
```

Value formats: Colors (FF0000, red, rgb(255,0,0)), Spacing (12pt, 0.5cm, 1.5x), Dimensions (2.54cm, 1in, 72pt).

### find — format or replace matched text

```bash
officecli set doc.docx '/body/p[1]' --prop find=weather --prop bold=true --prop color=red
officecli set doc.docx / --prop find=draft --prop replace=final
```

### add — add elements or clone

```bash
officecli add <file> <parent> --type <type> [--prop ...]
officecli add <file> <parent> --type <type> --after <path> [--prop ...]
officecli add <file> <parent> --from <path>  # clone existing element
```

### move, swap, remove

```bash
officecli move <file> <path> [--to <parent>] [--index N]
officecli swap <file> <path1> <path2>
officecli remove <file> '/body/p[4]'
```

### batch — multiple operations

```bash
echo '[{"command":"set","path":"/Sheet1/A1","props":{"value":"Name"}}]' | officecli batch data.xlsx --json
officecli batch data.xlsx --commands '[...]' --json
```

## L3: Raw XML

Use when L2 cannot express what you need.

```bash
officecli raw <file> <part>
officecli raw-set <file> <part> --xpath "..." --action replace --xml '<w:p>...</w:p>'
```

## Specialized Skills

Use `load_skill <name>` to load domain-specific skill files:

| Name | Description |
|------|-------------|
| `officecli-docx` | Word documents — reports, letters, memos, proposals |
| `officecli-pptx` | PowerPoint presentations — decks, slides |
| `officecli-xlsx` | Excel — spreadsheets, formulas, pivots |
| `officecli-academic-paper` | APA/Chicago/IEEE papers, equations, bibliography |
| `officecli-pitch-deck` | Fundraising investor pitch decks |
| `officecli-financial-model` | 3-statement, DCF, LBO financial models |
| `officecli-data-dashboard` | Excel KPI dashboards with charts |
| `officecli-word-form` | Fillable Word forms with content controls |
| `morph-ppt` | PowerPoint Morph transition decks |
| `morph-ppt-3d` | 3D Morph with GLB models |

## Common Pitfalls

| Pitfall | Correct Approach |
|---------|-----------------|
| Unquoted `[N]` paths | Always quote: `'/slide[1]'` |
| `--name "foo"` | Use `--prop name="foo"` |
| Guessing property names | Run `officecli help <format> <element>` |
| `$` in shell text | Single-quote: `--prop text='$15M'` |
| `\n` in shell strings | Use `\\n` for newlines |

## Notes

- Paths are **1-based** (XPath): `'/body/p[3]'` = third paragraph
- `--index` is **0-based**
- After modifications, verify with `validate` and `view issues`
- **When unsure**, run `officecli help <format> <element>`
