#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Run all Python tests in tests/ (each in its own process). Exit 0 only if all pass.

Usage: python tests/run_all.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main() -> int:
    tests = sorted(f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    failed = []
    for name in tests:
        path = os.path.join(HERE, name)
        print(f"\n===== {name} =====", flush=True)
        r = subprocess.run([sys.executable, path], cwd=ROOT)
        if r.returncode != 0:
            failed.append(name)

    print()
    if failed:
        print(f"FAILED: {len(failed)} / {len(tests)}: {', '.join(failed)}")
        return 1
    print(f"ALL PASSED: {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
