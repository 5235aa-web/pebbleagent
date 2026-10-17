#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ToolRegistry.auto_register_from_module: scans a module and registers its tools.

Run: python tests/test_tool_registry_autoregister.py   (exit 0 = pass)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tools.tool_registry import ToolRegistry

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


def main():
    ToolRegistry.clear()
    exc = None
    try:
        ToolRegistry.auto_register_from_module("tools.grep_tool")
    except Exception as e:
        exc = e
    names = ToolRegistry.list_tools()
    check("no exception", exc is None, repr(exc))
    check("grep registered", "grep" in names, names)
    check("search_codebase registered", "search_codebase" in names, names)
    ToolRegistry.clear()

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
