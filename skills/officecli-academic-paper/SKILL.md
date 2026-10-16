---
name: officecli-academic-paper
description: "Build academic-style .docx: journal/conference/thesis papers with formal citation styles (APA/Chicago/IEEE/MLA), numbered equations, figure/table cross-references, footnotes/endnotes, bibliography, multi-column journal layout."
---

# OfficeCLI Academic Paper Skill

**This skill is a scene layer on top of `officecli-docx`.** Load `officecli-docx` first for base rules.

## Setup

Follow install instructions from `officecli` skill.

## Citation Style Decision

| Style | In-text shape | Reference order | Line spacing |
|-------|--------------|-----------------|-------------|
| APA 7 | `(Smith, 2024)` | Alphabetical | 2x double |
| Chicago 17 | Superscript footnote | Alphabetical | 1.5x-2x |
| IEEE | `[1]`, `[2]` | Order of citation | 1.15x-1.5x |
| MLA 9 | `(Smith 412)` | Alphabetical | 2x |

## Key Operations

```bash
# Equations (OMML — display or inline)
officecli add "$FILE" /body --type equation --prop mode=display --prop formula="x^2 + y^2 = z^2"

# Footnotes
officecli add "$FILE" "/body/p[3]" --type footnote --prop text="Smith et al. 2023"

# Bibliography with hanging indent
officecli add "$FILE" /body --type paragraph \
  --prop text="Smith, J. (2024). Title. Journal, 109(3), 412-430." \
  --prop indent=720 --prop hangingIndent=720

# Figures with SEQ fields
officecli add "$FILE" /body --type picture --prop src=arch.png --prop width=5in
officecli add "$FILE" /body --type paragraph --prop text="Figure "
officecli add "$FILE" "/body/p[last()]" --type field --prop fieldType=seq --prop identifier=Figure

# Two-column layout (IEEE)
officecli add "$FILE" /body --type section --prop type=continuous
officecli set "$FILE" "/section[2]" --prop columns=2 --prop columnSpace=1cm

# Abstract block (no first-line indent)
officecli add "$FILE" /body --type paragraph --prop text="Abstract" --prop align=center --prop size=14pt --prop bold=true
```

## QA

Must pass `officecli-docx` Delivery Gate plus citation round-trip check and SEQ field presence.
