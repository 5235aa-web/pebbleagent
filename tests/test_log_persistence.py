#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Log persistence (A) + suspicious stream-end detection (C). Isolated via temp PEBBLE_HOME.

Run: python tests/test_log_persistence.py   (exit 0 = pass)
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    passed = 0
    failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    tmp_home = tempfile.mkdtemp(prefix="pebble_test_")
    os.environ["PEBBLE_HOME"] = tmp_home  # must be set before importing gateway modules

    print("=== Test 1: log_setup (A) ===")
    import logging
    from gateway.log_setup import setup_logging

    setup_logging()
    log_dir = os.path.join(tmp_home, "logs")
    log_file = os.path.join(log_dir, "backend.log")
    check("log dir created", os.path.isdir(log_dir))
    check("log file created", os.path.isfile(log_file))

    test_logger = logging.getLogger("test.verify")
    test_logger.info("HELLO_LOG_TEST_12345")
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass

    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    check("log content written", "HELLO_LOG_TEST_12345" in content, repr(content[:200]))
    check("file format has timestamp+level+name", "INFO:test.verify:" in content)

    n_before = len(logging.getLogger().handlers)
    setup_logging()
    n_after = len(logging.getLogger().handlers)
    check("idempotent (no duplicate handlers)", n_before == n_after, f"{n_before} -> {n_after}")

    print("=== Test 2: suspicious end detection (C) ===")
    from agent_core.agent_chat_service_v2 import _is_suspicious_stream_end as f

    check("length truncation -> suspicious", f("length", "some text") is True)
    check("length + empty -> suspicious", f("length", "") is True)
    check("stop + text -> normal", f("stop", "任务完成，报告如下") is False)
    check("stop + empty -> suspicious (silent stop)", f("stop", "") is True)
    check("stop + whitespace -> suspicious", f("stop", "   \n  ") is True)
    check("None + text -> normal", f(None, "done") is False)
    check("None + empty -> suspicious", f(None, "") is True)
    check("tool_calls + text -> normal", f("tool_calls", "let me check") is False)

    print(f"\n=== RESULT: PASS={passed} FAIL={failed} ===")
    shutil.rmtree(tmp_home, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
