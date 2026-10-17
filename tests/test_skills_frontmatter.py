#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Skill frontmatter parsing.

Covers: multi-line descriptions, values containing colons, body text that must
not be mistaken for frontmatter, invalid YAML fallback, and the live skills/
directory.

Run: python tests/test_skills_frontmatter.py   (exit 0 = pass)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import skills_loader as sl

passed = 0
failed = 0


def check(label: str, cond: bool) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {label}")
    else:
        failed += 1
        print(f"[FAIL] {label}")


multiline = """---
name: demo
description: |
  First line.
  Second line with: colon.
version: 1.0
---

# Body
description: this body line must be ignored
"""
desc = sl._extract_description(multiline)
check("multiline keeps first line", desc.startswith("First line."))
check("multiline keeps second line", "Second line with: colon." in desc)
check("body description ignored", "body line" not in desc)

quoted = """---
name: demo
description: "Use this: when the user asks"
---
body
"""
check("quoted colon value intact",
      sl._extract_description(quoted) == "Use this: when the user asks")

no_fm = """# Title
description: fallback single line
"""
check("no frontmatter falls back",
      sl._extract_description(no_fm) == "fallback single line")

bad_yaml = """---
name: demo
description: [unclosed
---
"""
check("invalid yaml does not raise",
      isinstance(sl._extract_description(bad_yaml), str))

names = sl.get_skill_names()
check("skills discovered", len(names) > 50)
check("all skill dirs are ascii", all(n.isascii() for n in names))

listed = {s["name"]: s["description"] for s in sl.list_skills()}
check("all descriptions non-empty", all(bool(v) for v in listed.values()))
print(f"\nRESULT: PASS={passed} FAIL={failed}")
sys.exit(0 if failed == 0 else 1)
