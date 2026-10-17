#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Agent tool policy: chat_agent 4-tool allowlist (YAML load + filter + policy check).

Run: python tests/test_agent_tool_policy.py   (exit 0 = pass)
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

    from agent_core.agent_loader import (
        load_agent_yaml,
        filter_tools_for_agent,
        check_agent_tool_policy,
        clear_agent_cache,
    )

    # ============ 1. chat_agent.yaml loads with enforce + allowlist ============
    clear_agent_cache()
    defn = load_agent_yaml("chat_agent")
    check("chat_agent loaded", defn is not None)
    check(
        "enforce=True",
        defn is not None and defn.tools.enforce is True,
        f"got {getattr(defn.tools, 'enforce', None) if defn else None}",
    )
    check(
        "allowed == 4 tools",
        defn is not None
        and set(defn.tools.allowed) == {"read_file", "search_files", "edit_file", "write_file"},
        f"got {defn.tools.allowed if defn else None}",
    )

    # ============ 2. filter_tools_for_agent ============
    fake_tools = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "web_search"}},
        {"type": "function", "function": {"name": "mcp__blender__look"}},
        {"type": "function", "function": {"name": "execute_code"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "search_files"}},
        {"type": "function", "function": {"name": "edit_file"}},
        {"type": "function", "function": {"name": "clarify"}},
    ]
    filtered = filter_tools_for_agent(fake_tools, defn)
    names = sorted(t["function"]["name"] for t in filtered)
    check(
        "filtered == 4 tools",
        names == ["edit_file", "read_file", "search_files", "write_file"],
        f"got {names}",
    )

    code_def = load_agent_yaml("code_agent")
    filtered2 = filter_tools_for_agent(fake_tools, code_def)
    check("code_agent unaffected (passthrough)", len(filtered2) == len(fake_tools), f"got {len(filtered2)}")

    filtered3 = filter_tools_for_agent(fake_tools, None)
    check("agent_def=None passthrough", len(filtered3) == len(fake_tools))

    # ============ 3. check_agent_tool_policy ============
    clear_agent_cache()
    for t in ["read_file", "write_file", "edit_file", "search_files"]:
        check(f"chat_agent + {t} -> allowed", check_agent_tool_policy("chat_agent", t) is None)
    r1 = check_agent_tool_policy("chat_agent", "web_search")
    check("chat_agent + web_search -> denied", r1 is not None, f"got {r1}")
    r2 = check_agent_tool_policy("chat_agent", "mcp__blender__look")
    check("chat_agent + MCP tool -> denied", r2 is not None, f"got {r2}")
    r3 = check_agent_tool_policy("chat_agent", "execute_code")
    check("chat_agent + execute_code -> denied", r3 is not None, f"got {r3}")
    check(
        "code_agent + web_search -> allowed (enforce off)",
        check_agent_tool_policy("code_agent", "web_search") is None,
    )
    check("empty agent -> allowed", check_agent_tool_policy("", "web_search") is None)
    check("unknown agent -> allowed", check_agent_tool_policy("nonexistent_agent", "web_search") is None)

    # ============ 4. Real full tool list end-to-end ============
    from agent_core.tool_definitions import get_tools_definition

    all_tools = get_tools_definition()
    all_names = [t["function"]["name"] for t in all_tools]
    print(f"[INFO] total base tools: {len(all_names)}")
    filtered_real = filter_tools_for_agent(all_tools, defn)
    real_names = sorted(t["function"]["name"] for t in filtered_real)
    check(
        "real full list -> filtered == 4 tools",
        real_names == ["edit_file", "read_file", "search_files", "write_file"],
        f"got {real_names}",
    )
    missing = {"read_file", "search_files", "edit_file", "write_file"} - set(all_names)
    check("4 target tools exist in full list", not missing, f"missing {missing}")
    for probe in ["execute_code", "execute_shell", "web_search", "fetch_url", "clarify"]:
        check(f"full list contains {probe} (will be filtered out)", probe in all_names)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
