#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Orchestration-layer cleanup (2026-10-17, review issue 8).

Covers: extracted _build_and_sanitize_tool_calls equivalence, _sanitize_messages
no longer mutating its input, unified token counting (single implementation),
dead-code removal (StreamEvent orphans / _execute_tool), and the merged tool
executor core.

Run: python tests/test_orchestration_cleanup.py   (exit 0 = pass)
"""
import os
import sys

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
            print(f"[PASS] {name}")
        else:
            failed += 1
            print(f"[FAIL] {name} {detail}")

    def read(path):
        with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
            return f.read()

    from agent_core.agent_chat_service_v2 import AgentChatServiceV2

    svc = AgentChatServiceV2.__new__(AgentChatServiceV2)  # no __init__ side effects

    # ---- _build_and_sanitize_tool_calls --------------------------------------
    tools = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "Write_File"}},
    ]

    check("build: empty chunks -> None", svc._build_and_sanitize_tool_calls({}, tools) is None)

    chunks = {
        0: {"id": "", "function": {"name": "read_file", "arguments": "{}"}},
        1: {"id": "ok1", "function": {"name": "write_file", "arguments": "{}"}},  # case fix
    }
    result = svc._build_and_sanitize_tool_calls(chunks, tools)
    check("build: empty id fixed", result[0]["id"].startswith("call_"), f"got {result[0]['id']}")
    check("build: case repaired", result[1]["function"]["name"] == "Write_File",
          f"got {result[1]['function']['name']}")

    chunks = {
        0: {"id": "a", "function": {"name": "", "arguments": "{}"}},
        1: {"id": "b", "function": {"name": "not_a_tool", "arguments": "{}"}},
        2: {"id": "c", "function": {"name": "read_file", "arguments": "{}"}},
    }
    result = svc._build_and_sanitize_tool_calls(chunks, tools)
    check("build: invalid dropped, valid kept",
          len(result) == 1 and result[0]["id"] == "c", f"got {result}")

    # ---- _sanitize_messages must not mutate its input ------------------------
    original_tc = {"function": {"name": "x", "arguments": "{}"}}  # no id
    original_msg = {"role": "assistant", "tool_calls": [original_tc]}
    messages = [original_msg]
    sanitized = svc._sanitize_messages(messages)
    check("sanitize: input tool_call object untouched", "id" not in original_tc,
          f"input mutated: {original_tc}")
    check("sanitize: output carries generated id",
          sanitized[0]["tool_calls"][0].get("id", "").startswith("fallback_tc_"))
    check("sanitize: input list object untouched", messages[0] is original_msg)

    # orphan tool message removal still works
    orphan = {"role": "tool", "tool_call_id": "nope", "content": "x"}
    sanitized2 = svc._sanitize_messages([orphan])
    check("sanitize: orphan tool removed", len(sanitized2) == 0)

    # ---- Unified token counting ----------------------------------------------
    from agent_core.agent_chat_service_v2 import count_tokens
    from agent_core.context_compressor import estimate_tokens, truncate_to_tokens

    sample = "hello world 你好世界"
    check("token: service count == compressor estimate",
          count_tokens(sample) == estimate_tokens(sample),
          f"{count_tokens(sample)} vs {estimate_tokens(sample)}")
    compressor_src = read("agent_core/context_compressor.py")
    check("token: rough //4 implementation removed",
          "rough_token_count_estimation" not in compressor_src)
    service_src = read("agent_core/agent_chat_service_v2.py")
    check("token: duplicated encoder cache removed",
          "_get_encoding" not in service_src and "len(text) // 3" not in service_src)
    out = truncate_to_tokens("x" * 5000, 10)
    check("token: truncate_to_tokens still works", len(out) < 5000 and "truncated" in out)

    # ---- Dead-code removal ----------------------------------------------------
    qsm_src = read("agent_core/query_state_machine.py")
    for dead in ("ChatMessage", "MessageType", "ContentEvent", "CompactEvent",
                 "ChatStartedEvent", "ChatCompletedEvent", "ErrorEvent",
                 "CancelledEvent", "parse_tool_calls_from_response"):
        # docstring mentions the removed names for changelog purposes; assert no
        # class/def definitions remain
        check(f"dead removed: {dead}",
              f"class {dead}" not in qsm_src and f"def {dead}" not in qsm_src)
    check("dead: ToolExecutionContext kept", "class ToolExecutionContext" in qsm_src)
    check("dead: ToolExecutionEvent kept", "class ToolExecutionEvent" in qsm_src)
    check("dead: _execute_tool method removed", not hasattr(svc, "_execute_tool"))

    # ---- Executor merge -------------------------------------------------------
    orch_src = read("agent_core/tool_orchestrator.py")
    check("merge: core exists", "async def _execute_tool_core" in orch_src)
    check("merge: queue wrapper delegates to core",
          "async for update in gen:" in orch_src and "await output_queue.put(update)" in orch_src)
    check("merge: single wrapper delegates to core",
          "async for update in self._execute_tool_core(tool_call, context):" in orch_src)

    # ---- Extracted logic used by chat_stream ----------------------------------
    check("extract: chat_stream calls the extracted method",
          "self._build_and_sanitize_tool_calls(tool_calls_chunks, tools)" in service_src)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
