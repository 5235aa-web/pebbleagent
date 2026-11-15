#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for cross-session message batch forwarding within the same project.

Run: python tests/test_message_forward.py
"""
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble_msg_forward_")
os.environ["PEBBLE_HOME"] = TMP
os.environ["PEBBLE_CONFIG_DIR"] = TMP

import gateway.server as sv  # noqa: E402
from agent_core import agent_chat_history as history  # noqa: E402

passed = 0
failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {label}")
    else:
        failed += 1
        print(f"[FAIL] {label} {detail}")


def main() -> int:
    proj_a = os.path.join(TMP, "proj_a")
    proj_b = os.path.join(TMP, "proj_b")
    os.makedirs(proj_a, exist_ok=True)
    os.makedirs(proj_b, exist_ok=True)

    # Create source, target (same project), and foreign (other project) sessions
    src = sv._session_store.create(title="Source Session", project_path=proj_a)
    dst = sv._session_store.create(title="Target Session", project_path=proj_a)
    other = sv._session_store.create(title="Other Project Session", project_path=proj_b)

    src_sid = src["session_id"]
    dst_sid = dst["session_id"]
    other_sid = other["session_id"]

    # Seed source session messages:
    # 1. User message
    u_id, u_uuid = sv._session_store.add_user_message(src_sid, "请帮我检查并修改 app.py")
    sv._session_store.increment_messages(src_sid)

    # 2. Assistant message with read_file (should be filtered), edit_file (short), execute_shell (long > 600 chars)
    long_shell_out = "LINE_START " + ("x" * 1400) + " LINE_END"
    eo = [
        {"type": "reasoning", "content": "internal thought", "order": 0},
        {
            "type": "tool",
            "order": 1,
            "tool_info": {
                "id": "tc_read_1",
                "name": "read_file",
                "input": '{"path": "app.py"}',
                "output": "READ_ONLY_SECRET_CONTENT_SHOULD_NOT_APPEAR",
                "status": "completed",
            },
        },
        {
            "type": "tool",
            "order": 2,
            "tool_info": {
                "id": "tc_grep_1",
                "name": "grep",
                "input": '{"pattern": "foo"}',
                "output": "GREP_CONTENT_SHOULD_NOT_APPEAR",
                "status": "completed",
            },
        },
        {
            "type": "tool",
            "order": 3,
            "tool_info": {
                "id": "tc_edit_1",
                "name": "edit_file",
                "input": '{"path": "app.py", "old": "a", "new": "b"}',
                "output": "Edited app.py successfully.",
                "status": "completed",
            },
        },
        {
            "type": "tool",
            "order": 3.5,
            "tool_info": {
                "id": "tc_write_1",
                "name": "write_file",
                "input": json.dumps({"file_path": "E:/pebbleagent/config.ini", "content": "A" * 2000}, ensure_ascii=False),
                "output": "Successfully wrote config.ini (2000 bytes).",
                "status": "completed",
            },
        },
        {
            "type": "tool",
            "order": 4,
            "tool_info": {
                "id": "tc_shell_1",
                "name": "execute_shell",
                "input": '{"command": "pytest"}',
                "output": long_shell_out,
                "status": "completed",
            },
        },
        {"type": "text", "content": "已完成修改并运行测试。", "order": 5},
    ]
    a_id, a_uuid = sv._session_store.add_assistant_message(
        src_sid,
        "已完成修改并运行测试。",
        reasoning="internal thought",
        tool_calls=json.dumps(eo, ensure_ascii=False),
    )
    sv._session_store.increment_messages(src_sid)

    # 3. Compaction summary card (> 2500 chars, wrapped with scaffolding)
    summary_body = "核心结论：" + ("重要上下文细节_" * 220) + "END_OF_SUMMARY"
    full_summary_stored = (
        "This session is being continued from a previous conversation that ran out of context. "
        "The summary below covers the earlier portion of the conversation.\n\n"
        + summary_body
        + "\n\nRecent messages are preserved verbatim."
    )
    from gateway.session import _get_db
    db = _get_db()
    next_ord = sv._session_store._next_order(src_sid)
    cur = db.execute(
        "INSERT INTO messages (session_id, role, content, msg_uuid, sort_order, is_compact_summary, created_at) "
        "VALUES (?, 'user', ?, 'sum-uuid-1', ?, 1, 1700000100.0)",
        (src_sid, full_summary_stored, next_ord),
    )
    db.commit()
    sum_id = cur.lastrowid

    # Track broadcast events
    events: list[tuple[str, dict]] = []
    sv.set_event_broadcaster(lambda et, payload: events.append((et, payload)))

    # 1. Forward out-of-order IDs -> should merge in source chronological order
    resp = sv.handle_request(
        "message.forward",
        {
            "source_session_id": src_sid,
            "target_session_id": dst_sid,
            "message_ids": [sum_id, a_id, u_id],
        },
        "fwd-1",
    )
    res = (resp or {}).get("result") or {}
    check("message.forward returns ok=True", res.get("ok") is True, f"resp={resp}")
    check("forwarded_count == 3", res.get("forwarded_count") == 3, f"res={res}")

    dst_msgs = sv._session_store.get_messages(dst_sid, limit=100)
    check("target sessions.db has exactly 1 merged message", len(dst_msgs) == 1, f"len={len(dst_msgs)}")
    merged_text = dst_msgs[0]["text"] if dst_msgs else ""
    check("merged message role is user", bool(dst_msgs and dst_msgs[0]["role"] == "user"))
    check("source header included", "Source Session" in merged_text)

    # Chronological order check
    pos_u = merged_text.find("请帮我检查并修改 app.py")
    pos_a = merged_text.find("已完成修改并运行测试。")
    pos_s = merged_text.find("核心结论：")
    check("chronological order preserved", 0 < pos_u < pos_a < pos_s, f"pos={(pos_u, pos_a, pos_s)}")

    # Read-only tool filtering
    check("read_file filtered out", "read_file" not in merged_text and "READ_ONLY_SECRET_CONTENT" not in merged_text)
    check("grep filtered out", "grep" not in merged_text and "GREP_CONTENT" not in merged_text)

    # Edit / execution tools retained and truncated <= 600 chars
    check("edit_file retained", "`edit_file`" in merged_text and "Edited app.py successfully." in merged_text)
    check("write_file retained with concise input summary", "`write_file`" in merged_text and "config.ini" in merged_text and "Successfully wrote config.ini" in merged_text)
    check("write_file hides huge raw content json", ("A" * 100) not in merged_text)
    check("execute_shell retained", "`execute_shell`" in merged_text)
    shell_blocks = re.findall(r"- `execute_shell` \[completed\]\n```text\n([\s\S]*?)\n```", merged_text)
    check("execute_shell block found", len(shell_blocks) == 1, f"blocks={shell_blocks}")
    if shell_blocks:
        check(
            "execute_shell execution content truncated <= 600 chars",
            len(shell_blocks[0]) <= 600 and "truncated" in shell_blocks[0],
            f"len={len(shell_blocks[0])}",
        )

    # prepare_only mode: returns formatted content without mutating DB or broadcasting
    events_before = len(events)
    prep_resp = sv.handle_request(
        "message.forward",
        {
            "source_session_id": src_sid,
            "target_session_id": dst_sid,
            "message_ids": [u_id, a_id, sum_id],
            "prepare_only": True,
        },
        "fwd-prep",
    )
    prep_res = (prep_resp or {}).get("result") or {}
    check("prepare_only returns ok=True and content", prep_res.get("ok") is True and prep_res.get("content") == merged_text)
    check("prepare_only does not add DB row", len(sv._session_store.get_messages(dst_sid, limit=100)) == 1)
    check("prepare_only does not broadcast event", len(events) == events_before)

    # Summary card full-content forwarding (no truncation, scaffolding stripped)
    check("summary card full body preserved", summary_body in merged_text and "END_OF_SUMMARY" in merged_text)
    check("summary scaffolding stripped", "ran out of context" not in merged_text)

    # Target session counter & agent_chat.db sync
    dst_info = sv._session_store.get(dst_sid) or {}
    check("target session message_count == 1", dst_info.get("message_count") == 1, f"dst_info={dst_info}")

    conv = history.get_agent_conversation_by_uuid(dst_sid)
    check("agent_chat.db conversation created for empty target", conv is not None)
    if conv:
        ctx_msgs = history.get_agent_chat_messages_for_context(conv["id"])
        check("agent_chat.db context has merged message", len(ctx_msgs) == 1 and ctx_msgs[0]["content"] == merged_text)

    # Distinct msg_uuid check (deleting target message must not touch source)
    dst_uuids = sv._session_store.get_message_uuids(dst_sid)
    src_uuids = sv._session_store.get_message_uuids(src_sid)
    check("new msg_uuid is distinct from source uuids", bool(dst_uuids) and dst_uuids[0] not in src_uuids)

    # Broadcast event check
    check(
        "message.forwarded event broadcast",
        any(et == "message.forwarded" and pl.get("target_session_id") == dst_sid for et, pl in events),
        f"events={events}",
    )

    # 2. Validation guards
    same_resp = sv.handle_request(
        "message.forward",
        {"source_session_id": src_sid, "target_session_id": src_sid, "message_ids": [u_id]},
        "fwd-2",
    )
    check("reject same-session forward", "error" in (same_resp or {}), f"resp={same_resp}")

    cross_resp = sv.handle_request(
        "message.forward",
        {"source_session_id": src_sid, "target_session_id": other_sid, "message_ids": [u_id]},
        "fwd-3",
    )
    check("reject cross-project forward", "error" in (cross_resp or {}), f"resp={cross_resp}")

    sv.set_session_running_probe(lambda sid: sid == dst_sid)
    try:
        busy_resp = sv.handle_request(
            "message.forward",
            {"source_session_id": src_sid, "target_session_id": dst_sid, "message_ids": [u_id]},
            "fwd-4",
        )
        check("reject forward while target session running", "error" in (busy_resp or {}), f"resp={busy_resp}")
    finally:
        sv.set_session_running_probe(None)

    print()
    if failed:
        print(f"FAILED: {failed} checks")
        return 1
    print(f"ALL PASSED: {passed} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
