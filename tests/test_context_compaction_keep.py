#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Context compaction: dynamic keep-count (no production data touched).

Run: python tests/test_context_compaction_keep.py   (exit 0 = pass)
"""
import os
import sys

# Must be set before importing the compressor: skips transcript persistence.
os.environ["PEBBLE_SKIP_PROMPT_HISTORY"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def make_msgs(n):
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": "message number %d" % i})
    return msgs


def main() -> int:
    passed = 0
    failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  [PASS] %s" % name)
        else:
            failed += 1
            print("  [FAIL] %s | %s" % (name, detail))

    from agent_core.context_compressor import manual_compress

    def run_case(name, msgs, expect_success, expect_keep=None, expect_reason_sub=None):
        print("=== %s (n=%d) ===" % (name, len(msgs)))
        result = manual_compress(messages=msgs, max_tokens=30000, recent_messages=10, llm_client=None)
        ok = bool(result.get("success"))
        check("success == %s" % expect_success, ok == expect_success,
              "got success=%s reason=%s" % (ok, result.get("reason")))
        if expect_success:
            compressed = result.get("compressed_messages", [])
            keep = max(0, len(compressed) - 2)  # [boundary] + [summary] + keep
            check("keep == %s" % expect_keep, keep == expect_keep,
                  "got keep=%d len(compressed)=%d" % (keep, len(compressed)))
            # structure check: first is boundary(system), second is summary(user)
            if len(compressed) >= 2:
                c0 = compressed[0]
                c1 = compressed[1]
                check("compressed[0] is boundary",
                      c0.get("role") == "system" and "[COMPACT_BOUNDARY:" in (c0.get("content") or ""),
                      str(c0.get("role")))
                check("compressed[1] is summary", c1.get("is_compact_summary") is True,
                      str(c1.get("is_compact_summary")))
            print("  before=%s after=%s saved=%s%%" % (result.get("before", {}).get("messages"),
                                                       result.get("after", {}).get("messages"),
                                                       result.get("saved_percent")))
        else:
            if expect_reason_sub:
                check("reason contains %r" % expect_reason_sub,
                      expect_reason_sub in (result.get("reason") or ""),
                      "got: %s" % result.get("reason"))

    # --- Scenario 1: exactly 2 messages -> compress 1, keep 1 ---
    run_case("2 messages", make_msgs(2), True, 1)

    # --- Scenario 2: 5 messages -> compress 1, keep 4 ---
    run_case("5 messages", make_msgs(5), True, 4)

    # --- Scenario 3: 11 messages -> compress 1, keep 10 ---
    run_case("11 messages", make_msgs(11), True, 10)

    # --- Scenario 4: 15 messages -> compress 5, keep 10 (unchanged behavior) ---
    run_case("15 messages", make_msgs(15), True, 10)

    # --- Scenario 5: 1 message -> fail ---
    run_case("1 message", make_msgs(1), False, None, "< 2")

    # --- Scenario 6: 0 messages -> fail ---
    run_case("0 messages", [], False, None, "Not enough")

    # --- Scenario 7: tool-pair boundary [assistant(tool_use), user(tool_result)] (2 msgs) ---
    # keep=1 would split the pair; protection expands keep to all -> summarize empty -> guarded fail
    msgs7 = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "foo", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ]
    run_case("tool-pair 2 msgs", msgs7, False, None, "没有消息需要压缩")

    # --- Scenario 8: [user, assistant(tool_use), user(tool_result)] (3 msgs) -> compress 1, keep 2 ---
    msgs8 = [
        {"role": "user", "content": "please run foo"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "foo", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
    ]
    run_case("tool-pair 3 msgs", msgs8, True, 2)

    print("\n=== RESULT: PASS=%d FAIL=%d ===" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
