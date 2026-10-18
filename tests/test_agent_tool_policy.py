#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Agent tool policy: chat_agent allowlist (YAML load + filter + policy check).

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

    # chat_agent.yaml 的白名单契约：内置读类 + 网络只读 + MCP 只读
    expected_internal = {
        "read_file", "search_files", "search_in_file", "grep", "glob",
        "web_search", "fetch_url", "browser_navigate",
    }
    expected_mcp = {
        "mcp__github__get_file_contents",
        "mcp__github__search_repositories",
        "mcp__github__search_code",
        "mcp__github__search_users",
        "mcp__github__list_commits",
        "mcp__github__get_issue",
        "mcp__github__list_issues",
        "mcp__github__search_issues",
        "mcp__github__get_pull_request",
        "mcp__github__list_pull_requests",
        "mcp__github__get_pull_request_files",
        "mcp__github__get_pull_request_status",
        "mcp__github__get_pull_request_comments",
        "mcp__github__get_pull_request_reviews",
        "mcp__chrome_devtools__list_pages",
        "mcp__chrome_devtools__navigate_page",
        "mcp__chrome_devtools__take_snapshot",
        "mcp__chrome_devtools__take_screenshot",
        "mcp__chrome_devtools__list_console_messages",
        "mcp__chrome_devtools__get_console_message",
        "mcp__chrome_devtools__list_network_requests",
        "mcp__chrome_devtools__get_network_request",
        "mcp__chrome_devtools__wait_for",
        "mcp__chrome_devtools__get_css_styles",
    }
    expected_allowed = expected_internal | expected_mcp

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
        "allowed == expected allowlist",
        defn is not None and set(defn.tools.allowed) == expected_allowed,
        f"diff: {sorted(set(defn.tools.allowed) ^ expected_allowed) if defn else None}",
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
        "filtered == allowlist intersection",
        names == ["read_file", "search_files", "web_search"],
        f"got {names}",
    )

    code_def = load_agent_yaml("code_agent")
    filtered2 = filter_tools_for_agent(fake_tools, code_def)
    check("code_agent unaffected (passthrough)", len(filtered2) == len(fake_tools), f"got {len(filtered2)}")

    filtered3 = filter_tools_for_agent(fake_tools, None)
    check("agent_def=None passthrough", len(filtered3) == len(fake_tools))

    # ============ 3. check_agent_tool_policy ============
    clear_agent_cache()
    for t in sorted(expected_internal):
        check(f"chat_agent + {t} -> allowed", check_agent_tool_policy("chat_agent", t) is None)
    for t in ["write_file", "edit_file", "delete_file", "execute_code", "execute_shell", "browser_download", "clarify", "list_tools"]:
        check(f"chat_agent + {t} -> denied", check_agent_tool_policy("chat_agent", t) is not None)
    r2 = check_agent_tool_policy("chat_agent", "mcp__blender__look")
    check("chat_agent + MCP tool -> denied", r2 is not None, f"got {r2}")
    r3 = check_agent_tool_policy("chat_agent", "execute_code")
    check("chat_agent + execute_code -> denied", r3 is not None, f"got {r3}")
    check(
        "code_agent + web_search -> allowed (empty allowlist, no filtering)",
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
        "real full list -> filtered == internal allowlist",
        real_names == sorted(expected_internal),
        f"got {real_names}",
    )
    missing = expected_internal - set(all_names)
    check("internal allowlist tools exist in full list", not missing, f"missing {missing}")
    for probe in ["execute_code", "execute_shell", "browser_download", "clarify"]:
        check(f"full list contains {probe} (will be filtered out)", probe in all_names)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
