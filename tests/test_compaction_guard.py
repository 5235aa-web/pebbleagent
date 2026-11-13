#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compaction guard: never fold a session that has not moved on.

A client-side timeout made the user retry a compaction the gateway had already
finished, and the retry summarized the summary, leaving two cards in the
transcript. This pins the rule that prevents it.

Run: python tests/test_compaction_guard.py   (exit 0 = pass)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def user(ts, summary=False):
    return {"role": "user", "text": "hello", "created_at": ts, "is_compact_summary": summary}


def assistant(ts):
    return {"role": "assistant", "text": "hi", "created_at": ts, "is_compact_summary": False}


def main() -> int:
    from gateway.rpc.session import _refuse_compaction

    passed = failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  [PASS] %s" % name)
        else:
            failed += 1
            print("  [FAIL] %s | %s" % (name, detail))

    # 1. first compaction: no summary yet
    check("first compaction is allowed",
          _refuse_compaction([user(100), assistant(101)]) is None)

    # 2. the incident: summary written, nothing said since
    rows = [user(100), assistant(101), {"role": "system", "text": "[COMPACT_BOUNDARY:x]",
                                        "created_at": 200, "is_compact_summary": False},
            user(200, summary=True)]
    reason = _refuse_compaction(rows)
    check("a retry with nothing new is refused", reason is not None, str(reason))
    check("the refusal explains itself", "nothing new to fold" in (reason or ""), str(reason))

    # 3. the kept rows of the previous compaction are older than the summary, so
    #    they must not count as "something new was said"
    rows = [user(50), assistant(51), user(200, summary=True), user(150), assistant(151)]
    check("kept rows older than the summary do not count", _refuse_compaction(rows) is not None)

    # 4. a real message after the summary opens it again
    rows = [user(100), assistant(101), user(200, summary=True), user(300), assistant(301)]
    check("a message after the summary is allowed", _refuse_compaction(rows) is None)

    # 5. several summaries: the newest one is the reference
    rows = [user(100, summary=True), user(200, summary=True), user(250), assistant(251)]
    check("the newest summary is the reference", _refuse_compaction(rows) is None)

    # 6. only summaries, no conversation at all
    check("summaries alone are refused",
          _refuse_compaction([user(100, summary=True)]) is not None)

    print("\n=== RESULT: PASS=%d FAIL=%d ===" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
