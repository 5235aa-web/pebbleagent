#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Run all Python tests in tests/ (each in its own process). Exit 0 only if all pass.

Usage: python tests/run_all.py
"""
import os
import subprocess
import sys

# Child output is decoded with `errors="replace"` and reprinted by this process.
# A Windows console or redirect defaults to the locale codec, so a single
# unencodable byte killed the runner itself before the summary could print. The
# test scripts already reconfigure stdout for the same reason.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main() -> int:
    tests = sorted(f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    failed = []
    for name in tests:
        path = os.path.join(HERE, name)
        print(f"\n===== {name} =====", flush=True)
        try:
            r = subprocess.run(
                [sys.executable, path],
                cwd=ROOT,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=600,
            )
            out = (r.stdout or "") + (r.stderr or "")
            code = r.returncode
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout if isinstance(exc.stdout, str) else "") + (
                exc.stderr if isinstance(exc.stderr, str) else ""
            )
            out += "\n[run_all] timed out after 600s\n"
            code = 1
        sys.stdout.write(out)
        if out and not out.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
        if code != 0:
            # The tail is repeated next to the summary: a CI log is usually read
            # from the bottom, and the failing assertion is the whole point.
            failed.append((name, "\n".join(out.strip().splitlines()[-25:])))

    print()
    if failed:
        msg = f"FAILED: {len(failed)} / {len(tests)}: {', '.join(n for n, _ in failed)}"
        print(msg)
        for name, tail in failed:
            print(f"\n----- {name} (last {len(tail.splitlines())} lines) -----")
            print(tail)
        # GitHub Actions annotation (ignored outside CI): makes the failing
        # test names visible in the run's annotations API without opening logs.
        print(f"::error::{msg}")
        return 1
    print(f"ALL PASSED: {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
