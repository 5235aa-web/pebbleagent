#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compaction markers: the newest messages stay in the LLM context.

Run: python tests/test_compaction_marker_keep.py   (exit 0 = pass)
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="pebble_compact_marker_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent_core import agent_chat_history as history  # noqa: E402

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("[PASS] " + name)
    else:
        failed += 1
        print("[FAIL] " + name + " " + detail)


def main() -> int:
    conv = history.create_agent_conversation("user", "code_agent", "compaction test")
    cid = conv["conversation_id"]
    for i in range(6):
        history.add_agent_chat_message(cid, "user" if i % 2 == 0 else "assistant", "msg %d" % i)

    ok = history.insert_compaction_result(
        cid,
        {"content": "[COMPACT_BOUNDARY:first]"},
        {"content": "summary one"},
        kept_count=2,
    )
    check("marker insert returns true", ok)

    msgs = history.get_agent_chat_messages_for_context(cid, limit=999999)
    texts = [m.get("content") or "" for m in msgs]
    check("newest message kept", "msg 5" in texts, str(texts))
    check("second newest kept", "msg 4" in texts, str(texts))
    check("older messages compacted", "msg 3" not in texts, str(texts))
    check("summary present", "summary one" in texts, str(texts))

    ok2 = history.insert_compaction_result(
        cid,
        {"content": "[COMPACT_BOUNDARY:second]"},
        {"content": "summary two"},
        kept_count=2,
    )
    check("second marker insert returns true", ok2)

    msgs2 = history.get_agent_chat_messages_for_context(cid, limit=999999)
    texts2 = [m.get("content") or "" for m in msgs2]
    check("newest message kept after second compaction", "msg 5" in texts2, str(texts2))
    check("second newest kept after second compaction", "msg 4" in texts2, str(texts2))
    check("old summary compacted", "summary one" not in texts2, str(texts2))
    check("new summary present", "summary two" in texts2, str(texts2))

    # SessionStore: sort_order gaps from delete_message must not swallow the kept user row
    from gateway.session import SessionStore
    store = SessionStore()
    sess = store.create(title="gap test")
    sid = sess["session_id"]
    store.add_user_message(sid, "u1")
    store.add_assistant_message(sid, "a1")
    store.add_user_message(sid, "u2")
    gap_id, _ = store.add_tool_message(sid, "read_file", "foo")
    store.add_assistant_message(sid, "a2")
    store.delete_message(sid, gap_id)  # leaves sort_order gap between u2 (2) and a2 (4)

    ok_s = store.insert_compaction_result(
        sid,
        {"content": "[COMPACT_BOUNDARY:sess1]"},
        {"content": "session summary"},
        kept_count=2,
    )
    check("SessionStore insert_compaction_result returns true", ok_s)
    ctx = store.get_messages_for_context(sid)
    ctx_roles_texts = [(m.get("role"), m.get("text")) for m in ctx]
    check("SessionStore keeps both u2 and a2 across sort_order gap",
          ("user", "u2") in ctx_roles_texts and ("assistant", "a2") in ctx_roles_texts,
          str(ctx_roles_texts))
    check("SessionStore compacted u1 and a1",
          ("user", "u1") not in ctx_roles_texts and ("assistant", "a1") not in ctx_roles_texts,
          str(ctx_roles_texts))

    print()
    if failed:
        print("FAILED: %d checks" % failed)
        return 1
    print("ALL PASSED: %d checks" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
