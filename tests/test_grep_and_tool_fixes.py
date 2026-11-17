#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression checks for upgraded grep, unified lsp, edit_file search_block forwarding,
and powershell process-termination confirmation.

Run: python tests/test_grep_and_tool_fixes.py   (exit 0 = pass)
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent_core import tool_definitions
from agent_core.tool_executor_impl import AgentToolExecutorImpl
from agent_core.tool_impl import filesystem as fs_mod
from agent_core.tool_impl.registry import TOOL_HANDLERS
from gateway.permission_manager import PermissionManager

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
    tmp = tempfile.mkdtemp(prefix="pebble-grep-fix-")
    try:
        # Build fixture tree
        os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
        os.makedirs(os.path.join(tmp, ".github", "workflows"), exist_ok=True)
        os.makedirs(os.path.join(tmp, "ignored_dir"), exist_ok=True)

        with open(os.path.join(tmp, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("ignored_dir/\n")

        with open(os.path.join(tmp, "src", "sample.ts"), "w", encoding="utf-8") as f:
            f.write("export const STATUS = 'STREAMING';\n")
            f.write("const out = foo(bar) + arr[0];\n")
            f.write("const path = 'C:\\\\Users\\\\admin';\n")

        with open(os.path.join(tmp, "src", "view.vue"), "w", encoding="utf-8") as f:
            f.write("<template><div>StreamHead</div></template>\n")

        with open(os.path.join(tmp, "src", "many.txt"), "w", encoding="utf-8") as f:
            for i in range(10):
                f.write(f"hit_line_{i}\n")

        with open(os.path.join(tmp, ".github", "workflows", "ci.yml"), "w", encoding="utf-8") as f:
            f.write("name: workflow_ci\n")

        with open(os.path.join(tmp, "ignored_dir", "secret_note.txt"), "w", encoding="utf-8") as f:
            f.write("ignored_marker_token\n")

        ex = AgentToolExecutorImpl(project_path=tmp)

        # ---- 1. grep (ripgrep path) ----
        # Smart-case: lowercase matches uppercase STREAMING
        r_smart = asyncio.run(ex._tool_grep("streaming"))
        data_smart = json.loads(r_smart)
        check("grep rg smart-case finds STREAMING", data_smart["numFiles"] == 1, r_smart)

        # Literal fallback on code with regex metacharacters foo(bar) and arr[0]
        r_lit = asyncio.run(ex._tool_grep("foo(bar) + arr[0]", output_mode="content"))
        data_lit = json.loads(r_lit)
        check("grep rg literal fallback matches foo(bar) + arr[0]",
              "foo(bar) + arr[0]" in data_lit["content"], r_lit)

        # Explicit dot-dir path auto-enables hidden
        r_dot = asyncio.run(ex._tool_grep("workflow_ci", path=".github"))
        data_dot = json.loads(r_dot)
        check("grep rg explicit dot-dir finds .github/workflows/ci.yml",
              data_dot["numFiles"] == 1, r_dot)

        # Comma-separated glob (*.vue,*.ts)
        r_glob = asyncio.run(ex._tool_grep("StreamHead|STREAMING", glob_pattern="*.vue,*.ts"))
        data_glob = json.loads(r_glob)
        check("grep rg comma-separated glob matches both .vue and .ts",
              data_glob["numFiles"] == 2, r_glob)

        # no_ignore finds ignored_dir
        r_noign = asyncio.run(ex._tool_grep("ignored_marker_token", no_ignore=True))
        data_noign = json.loads(r_noign)
        check("grep rg no_ignore=True searches ignored_dir", data_noign["numFiles"] == 1, r_noign)

        # head_limit reports totalLines & truncated=True
        r_trunc = asyncio.run(ex._tool_grep("hit_line_", output_mode="content", head_limit=3))
        data_trunc = json.loads(r_trunc)
        check("grep rg head_limit sets truncated=True and totalLines=10",
              data_trunc["numLines"] == 3 and data_trunc["totalLines"] == 10 and data_trunc["truncated"] is True,
              r_trunc)

        # Single-file count mode works
        r_cnt = asyncio.run(ex._tool_grep("hit_line_", path="src/many.txt", output_mode="count"))
        data_cnt = json.loads(r_cnt)
        check("grep rg single-file count mode returns count=10",
              data_cnt["numFiles"] == 1 and data_cnt["counts"][0]["count"] == 10,
              r_cnt)

        # ---- 2. grep (pure-Python fallback when rg is absent) ----
        orig_resolve_rg = fs_mod._resolve_rg
        fs_mod._resolve_rg = lambda: None
        try:
            r_py1 = asyncio.run(ex._tool_grep("streaming"))
            check("grep python fallback smart-case finds STREAMING",
                  json.loads(r_py1)["numFiles"] == 1, r_py1)

            r_py2 = asyncio.run(ex._tool_grep("foo(bar) + arr[0]", output_mode="content"))
            check("grep python fallback literal fallback matches foo(bar) + arr[0]",
                  "foo(bar) + arr[0]" in json.loads(r_py2)["content"], r_py2)

            r_py3 = asyncio.run(ex._tool_grep("workflow_ci", path=".github"))
            check("grep python fallback explicit dot-dir finds ci.yml",
                  json.loads(r_py3)["numFiles"] == 1, r_py3)

            r_py4 = asyncio.run(ex._tool_grep("StreamHead|STREAMING", glob_pattern="*.vue,*.ts"))
            check("grep python fallback comma-separated glob matches 2 files",
                  json.loads(r_py4)["numFiles"] == 2, r_py4)

            r_py5 = asyncio.run(ex._tool_grep("hit_line_", output_mode="content", head_limit=3))
            d_py5 = json.loads(r_py5)
            check("grep python fallback head_limit sets truncated=True",
                  d_py5["numLines"] == 3 and d_py5["totalLines"] == 10 and d_py5["truncated"] is True,
                  r_py5)
        finally:
            fs_mod._resolve_rg = orig_resolve_rg

        # ---- 3. edit_file search_block / replace_block forwarding ----
        target_edit = os.path.join(tmp, "src", "edit_me.py")
        with open(target_edit, "w", encoding="utf-8") as f:
            f.write("def greet(name):\n    return f'hi {name}'\n")
        edit_handler = TOOL_HANDLERS["edit_file"]
        r_edit = asyncio.run(edit_handler(ex, {
            "file_path": target_edit,
            "search_block": "def greet(name):\n    return f'hi {name}'",
            "replace_block": "def greet(name):\n    return f'hello {name}'",
        }, None))
        check("edit_file registry forwards search_block/replace_block",
              r_edit.startswith("Success:"), r_edit)
        with open(target_edit, "r", encoding="utf-8") as f:
            check("edit_file search_block updated file content", "hello {name}" in f.read())

        # ---- 4. lsp conditional schema & unified dispatch ----
        orig_lsp_check = tool_definitions._is_lsp_schema_enabled
        try:
            tool_definitions._is_lsp_schema_enabled = lambda: False
            names_no_lsp = {t["function"]["name"] for t in tool_definitions.get_tools_definition()}
            check("lsp schema omitted when LSP server unavailable", "lsp" not in names_no_lsp)

            tool_definitions._is_lsp_schema_enabled = lambda: True
            names_with_lsp = {t["function"]["name"] for t in tool_definitions.get_tools_definition()}
            check("lsp schema included when LSP server available", "lsp" in names_with_lsp)
        finally:
            tool_definitions._is_lsp_schema_enabled = orig_lsp_check

        r_bad_lsp = asyncio.run(TOOL_HANDLERS["lsp"](ex, {"action": "nonexistent"}, None))
        check("lsp unknown action returns clear error", r_bad_lsp.startswith("Error: unknown lsp action"), r_bad_lsp)

        # ---- 5. powershell process termination confirmation ----
        rules_path = os.path.join(tmp, "perm.json")
        with open(rules_path, "w", encoding="utf-8") as f:
            json.dump({"mode": "manual", "allow": ["powershell:*"], "deny": [], "ask": []}, f)
        pm = PermissionManager(config_path=rules_path)
        ok_git, _ = pm.check_permission_sync("powershell", "git status")
        check("manual allow powershell:* allows git status", ok_git is True)
        ok_kill1, why1 = pm.check_permission_sync("powershell", "taskkill /F /PID 1234")
        check("manual powershell taskkill requires confirmation", ok_kill1 is False and "confirmation" in why1, why1)
        ok_kill2, why2 = pm.check_permission_sync("powershell", "stop-process -Id 1234 -Force")
        check("manual powershell Stop-Process requires confirmation", ok_kill2 is False and "confirmation" in why2, why2)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
