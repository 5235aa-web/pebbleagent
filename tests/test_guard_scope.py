#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Guard scope isolation.

Covers: per-session counters for rate/navigation/download guards, scoped
resets, contextvar fallback, and asyncio task isolation.

Run: python tests/test_guard_scope.py   (exit 0 = pass)
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent_core.little_guard import (
    DownloadGuard,
    PathDepthGuard,
    RateLimiter,
    get_download_guard,
    get_guard_scope,
    get_rate_limiter,
    reset_all_guards,
    reset_guard_scope,
    set_guard_scope,
)

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


def main() -> int:
    rl = RateLimiter(max_per_minute=2)
    check("rate A first", rl.check("https://example.com/a", "A")[0])
    check("rate A second", rl.check("https://example.com/b", "A")[0])
    ok, msg = rl.check("https://example.com/c", "A")
    check("rate A limited", (not ok) and "Rate limited" in msg, msg)
    check("rate B independent", rl.check("https://example.com/d", "B")[0])
    rl.reset("B")
    check("reset B keeps A limited", not rl.check("https://example.com/e", "A")[0])
    check("reset B clears B", rl.check("https://example.com/f", "B")[0])

    dg = DownloadGuard(max_files_per_turn=1)
    check("download A first", dg.check_download("https://x/a.pdf", "", "A")[0])
    check("download A limited", not dg.check_download("https://x/b.pdf", "", "A")[0])
    check("download B independent", dg.check_download("https://x/c.pdf", "", "B")[0])
    dg.reset("A")
    check("download A cleared", dg.check_download("https://x/d.pdf", "", "A")[0])

    pg = PathDepthGuard(max_navigations_per_turn=2)
    check("nav A 1", pg.check_navigate("https://x/p", "A")[0])
    check("nav A 2", pg.check_navigate("https://x/q", "A")[0])
    check("nav A limited", not pg.check_navigate("https://x/r", "A")[0])
    check("nav B independent", pg.check_navigate("https://x/s", "B")[0])
    pg.reset("A")
    check("nav A cleared", pg.check_navigate("https://x/t", "A")[0])

    check("default scope", get_guard_scope() == "default")
    token = set_guard_scope("S1")
    check("scope set", get_guard_scope() == "S1")
    reset_guard_scope(token)
    check("scope restored", get_guard_scope() == "default")

    async def run():
        results = {}

        async def worker(name):
            t = set_guard_scope(name)
            await asyncio.sleep(0)
            results[name] = get_guard_scope()
            reset_guard_scope(t)

        await asyncio.gather(worker("X"), worker("Y"))
        return results

    res = asyncio.run(run())
    check("task isolation", res == {"X": "X", "Y": "Y"}, str(res))

    reset_all_guards()
    singleton_rate = get_rate_limiter()
    for _ in range(singleton_rate.max_per_minute):
        singleton_rate.check("https://example.com/g", "G")
    check("singleton limited", not singleton_rate.check("https://example.com/g", "G")[0])
    singleton_download = get_download_guard()
    for _ in range(singleton_download.max_files):
        singleton_download.check_download("https://x/g.pdf", "", "G")
    check("singleton download limited", not singleton_download.check_download("https://x/h.pdf", "", "G")[0])
    reset_all_guards()
    check("global reset", singleton_rate.check("https://example.com/g", "G")[0])
    check("global reset download", singleton_download.check_download("https://x/g.pdf", "", "G")[0])

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
