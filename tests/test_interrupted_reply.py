#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Interrupted-reply persistence at the gateway layer.

A transport error (peer closed mid-chunk) never reaches message.complete, so the
gateway has to persist what the user already saw. These checks pin the format:
body + blank line + **Error:** line, interrupted flag set, and the agent-side
uuid forwarded so both databases stay deletable in sync.

Runs against a temporary PEBBLE_HOME (no production data).
Run: python tests/test_interrupted_reply.py   (exit 0 = pass)
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = tempfile.mkdtemp(prefix="pebble-interrupted-")
os.environ["PEBBLE_HOME"] = TMP
os.environ.pop("PEBBLE_CONFIG_DIR", None)

from gateway import server
from gateway import session as session_store

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


def make_session(sid):
    conn = session_store._get_db()
    now = time.time()
    conn.execute(
        "INSERT INTO sessions (id, title, preview, created_at, updated_at,"
        " project_path, message_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, "t", "", now, now, TMP, 0),
    )
    conn.commit()


def last_message(sid):
    conn = session_store._get_db()
    row = conn.execute(
        "SELECT content, reasoning, tool_calls, interrupted, msg_uuid FROM messages"
        " WHERE session_id = ? ORDER BY sort_order DESC LIMIT 1",
        (sid,),
    ).fetchone()
    return dict(row) if row else None


def case_body_kept():
    print("\n=== partial body survives the error ===")
    make_session("s-body")
    ok, message_id = server._persist_interrupted_reply(
        "s-body",
        {
            "content": "half an answer",
            "reasoning": "some thinking",
            "assistant_msg_uuid": "uuid-1",
            "execution_order": [
                {"type": "reasoning", "content": "some thinking", "order": 0},
                {"type": "text", "content": "half an answer", "order": 1},
            ],
        },
        "",
        "",
        [],
        "peer closed connection without sending complete message body (incomplete chunked read)",
    )
    row = last_message("s-body")
    check("write reported success", bool(ok) and bool(message_id), (ok, message_id))
    check(
        "body precedes the error line",
        bool(row)
        and row["content"].startswith("half an answer\n\n**Error:** peer closed"),
        row,
    )
    check("reasoning kept", bool(row) and row["reasoning"] == "some thinking", row)
    check("interrupted flag set", bool(row) and row["interrupted"] == 1, row)
    check("agent uuid forwarded", bool(row) and row["msg_uuid"] == "uuid-1", row)
    check(
        "execution order stored",
        bool(row) and "half an answer" in (row["tool_calls"] or ""),
        row,
    )


def case_empty_body():
    print("\n=== nothing generated yet ===")
    make_session("s-empty")
    ok, _ = server._persist_interrupted_reply("s-empty", None, "", "", [], "boom")
    row = last_message("s-empty")
    check("still persisted", bool(ok), ok)
    check("error line only", bool(row) and row["content"] == "**Error:** boom", row)
    check("no stray blank lines", bool(row) and "\n\n" not in row["content"], row)


def case_gateway_buffer_fallback():
    print("\n=== gateway buffer used when the event carries no text ===")
    make_session("s-buffer")
    server._persist_interrupted_reply(
        "s-buffer", {"content": ""}, "text only in the gateway", "", [], "boom"
    )
    row = last_message("s-buffer")
    check(
        "buffer text kept",
        bool(row) and row["content"].startswith("text only in the gateway\n\n**Error:** boom"),
        row,
    )


def case_prompt_pipeline():
    print("\n=== error event persists one interrupted row ===")
    make_session("s-pipeline")

    class FakeService:
        _current_project_path = TMP

        async def chat_stream(self, **kwargs):
            yield {"type": "content", "content": "half an answer"}
            yield {
                "type": "error",
                "message": "peer closed connection without sending complete message body",
                "content": "half an answer",
                "reasoning": None,
                "execution_order": [
                    {"type": "text", "content": "half an answer", "order": 0}
                ],
                "assistant_msg_uuid": "uuid-pipeline",
                "interrupted": True,
            }

    original = server._ensure_service
    server._ensure_service = lambda: FakeService()
    events = []
    try:
        server.handle_prompt_submit(
            "s-pipeline", "hi", lambda t, p: events.append((t, p))
        )
    finally:
        server._ensure_service = original

    conn = session_store._get_db()
    rows = conn.execute(
        "SELECT content, interrupted FROM messages"
        " WHERE session_id = ? AND role = 'assistant' ORDER BY sort_order",
        ("s-pipeline",),
    ).fetchall()
    stored = [dict(r) for r in rows]
    check("exactly one assistant row", len(stored) == 1, stored)
    check(
        "row keeps the body",
        bool(stored)
        and stored[0]["content"].startswith("half an answer\n\n**Error:** peer closed"),
        stored,
    )
    check("row marked interrupted", bool(stored) and stored[0]["interrupted"] == 1, stored)
    kinds = [t for t, _ in events]
    check("error pushed", "error" in kinds, kinds)
    check("no message.complete after error", "message.complete" not in kinds, kinds)


def main():
    case_body_kept()
    case_empty_body()
    case_gateway_buffer_fallback()
    case_prompt_pipeline()
    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
