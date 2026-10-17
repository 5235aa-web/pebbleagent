#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""clarify multi-question dual-channel (gateway.server._submit_clarify_answer).

Run: python tests/test_clarify_dual.py   (exit 0 = pass)
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
    os.environ["PEBBLE_HOME"] = tmp_home  # must be set before importing gateway.server

    from tools.ask_question_tool import QuestionManager
    from gateway.server import _submit_clarify_answer

    print("=== Test 1: new channel (structured answers object) ===")
    QuestionManager.set_pending_questions({"id": "req-1", "questions": []})
    _submit_clarify_answer("a1; a2", "req-1", {"问题1文本": "答案1", "问题2文本": "答案2"})
    got = QuestionManager._answers
    check("answers stored as object", got == {"answers": {"问题1文本": "答案1", "问题2文本": "答案2"}}, repr(got))
    check("pending cleared after submit", QuestionManager.get_pending_questions() is None)

    print("=== Test 2: legacy channel (single answer string) ===")
    QuestionManager.set_pending_questions({"id": "req-2", "questions": []})
    _submit_clarify_answer("单答案", "req-2")
    got = QuestionManager._answers
    check("legacy wrapped as {'0': ...}", got == {"answers": {"0": "单答案"}}, repr(got))

    print("=== Test 3: request_id mismatch guard ===")
    QuestionManager.set_pending_questions({"id": "req-3", "questions": []})
    _submit_clarify_answer("x", "wrong-id", {"k": "v"})
    check("mismatch ignored (answers stays None)", QuestionManager._answers is None)
    check("pending kept", QuestionManager.get_pending_questions() is not None)

    print("=== Test 4: no-pending guard ===")
    QuestionManager.set_pending_questions({"id": "req-4", "questions": []})
    _submit_clarify_answer("ok", "req-4", {"k": "v"})
    check("cleared after valid submit", QuestionManager.get_pending_questions() is None)
    before = QuestionManager._answers
    _submit_clarify_answer("late", "", {"k2": "v2"})
    check("late submit ignored", QuestionManager._answers == before)

    print("=== Test 5: end-to-end shape (as consumed by _tool_clarify) ===")
    QuestionManager.set_pending_questions({"id": "req-5", "questions": []})
    _submit_clarify_answer("", "req-5", {"Q1": "A1", "Q2": "A2"})
    answers = asyncio.run(QuestionManager.wait_for_answers(timeout=1.0))
    check("wait_for_answers returns wrapper", answers == {"answers": {"Q1": "A1", "Q2": "A2"}}, repr(answers))
    inner = (answers or {}).get("answers", {})
    check("tool extraction gives {Q1: A1, Q2: A2}", inner == {"Q1": "A1", "Q2": "A2"}, repr(inner))

    print(f"\n=== RESULT: PASS={passed} FAIL={failed} ===")
    shutil.rmtree(tmp_home, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
