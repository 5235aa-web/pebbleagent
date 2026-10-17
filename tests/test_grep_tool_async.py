#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GrepTool async path: module-level asyncio import and one real search.

Run: python tests/test_grep_tool_async.py   (exit 0 = pass)
"""
import asyncio
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tools.grep_tool as grep_mod
from tools.grep_tool import GrepTool

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name} {detail}")


async def main():
    check("module exposes asyncio", hasattr(grep_mod, "asyncio"))

    tmp = tempfile.mkdtemp(prefix="pebble-grep-")
    with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as f:
        f.write("needle here\n")

    tool = GrepTool()
    exc = None
    result = None
    try:
        result = await tool.call({"pattern": "needle", "path": tmp, "output_mode": "files_with_matches"})
    except Exception as e:
        exc = e

    check("call raises no NameError", not isinstance(exc, NameError), repr(exc))
    if shutil.which("rg") and exc is None:
        check("returns dict", isinstance(result, dict), type(result).__name__)
        check("mode echoed", isinstance(result, dict) and result.get("mode") == "files_with_matches", result)
        check("finds the file", isinstance(result, dict) and any("a.txt" in x for x in result.get("filenames", [])), result)

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
