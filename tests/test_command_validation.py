#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Command/code validation parity.

execute_shell, powershell and execute_code(bash) share destructive/path
validation, so a destructive command cannot run by switching the entry tool.

Covers: PowerShellValidator matrix, code_destructive_guard matrix, bash-script
validation via _tool_execute_code, and integration through the private tool
methods (blocked cases return before any real execution).

Run: python tests/test_command_validation.py   (exit 0 = pass)
"""
import asyncio
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

    from agent_core.powershell_validator import PowerShellValidator
    from agent_core.code_destructive_guard import scan_code_for_destructive_behavior

    # ================= PowerShellValidator =================
    v = PowerShellValidator(project_path=ROOT)

    ps_block = [
        "Format-Volume -DriveLetter C",
        "Clear-Disk -Number 0",
        "diskpart",
        "bcdedit /set safeboot minimal",
        "vssadmin delete shadows /all",
        "cipher /w:C",
        "Stop-Computer -Force",
        "Remove-Item -Recurse -Force C:\\",
        "Remove-Item -Recurse -Force C:\\Windows",
        "del /s /q C:\\",
        "rd /s /q D:\\",
        "Remove-Item -Recurse $env:SystemRoot",
        "Remove-Item -Recurse -Force ~",
        "rm -rf /",
        "format C:",
    ]
    for cmd in ps_block:
        r = v.validate(cmd)
        check(f"PS block {cmd!r}", r.blocked, f"got blocked={r.blocked} reason={r.block_reason!r}")

    ps_allow = [
        "Get-ChildItem .\\src",
        "Remove-Item .\\build -Recurse",
        "Copy-Item .\\a.txt .\\b.txt",
        "Get-Content README.md",
        "Write-Output \"hello world\"",
        "Remove-Item $env:TEMP\\pebble_test -Recurse",
        "New-Item -ItemType Directory -Path .\\out",
    ]
    for cmd in ps_allow:
        r = v.validate(cmd)
        check(f"PS allow {cmd!r}", not r.blocked, f"got reason={r.block_reason!r}")

    r = v.validate("Set-ExecutionPolicy RemoteSigned")
    check("PS warn Set-ExecutionPolicy", not r.blocked and len(r.warnings) > 0)
    r = v.validate("Remove-Item .\\x.txt")
    check("PS warn Remove-Item", not r.blocked and any("删除" in w for w in r.warnings))

    # ================= code_destructive_guard =================
    code_block = [
        'os.system("rm -rf /")',
        'shutil.rmtree("/")',
        'shutil.rmtree("C:\\\\")',
        'subprocess.run("del /s /q C:\\\\", shell=True)',
        'os.system("format C:")',
        'os.system("mkfs.ext4 /dev/sda1")',
    ]
    for code in code_block:
        blocked, patterns = scan_code_for_destructive_behavior(code, "python")
        check(f"code block {code!r}", blocked, f"patterns={patterns}")

    code_allow = [
        'print("hello")',
        'shutil.rmtree("./build")',
        'x = "remove files"',
        'subprocess.run(["git", "status"])',
        'data = "a:b"',
    ]
    for code in code_allow:
        blocked, patterns = scan_code_for_destructive_behavior(code, "python")
        check(f"code allow {code!r}", not blocked, f"patterns={patterns}")

    # ================= Integration via private tool methods =================
    from agent_core.tool_executor_impl import AgentToolExecutorImpl

    ex = AgentToolExecutorImpl(project_path=ROOT)

    r = asyncio.run(ex._tool_execute_code('os.system("rm -rf /")', "python", None))
    check("execute_code destructive blocked at entry",
          r.startswith("⛔") and "破坏性" in r, f"got: {r[:80]}")

    r = asyncio.run(ex._tool_execute_code('del /s /q C:\\', "bash", None))
    check("execute_code bash destructive blocked at entry",
          r.startswith("⛔"), f"got: {r[:80]}")

    r = asyncio.run(ex._tool_execute_code('rm -rf /', "bash", None))
    check("execute_code bash rm-rf-root blocked at entry",
          r.startswith("⛔") or r.startswith("Error:"), f"got: {r[:80]}")

    r = asyncio.run(ex._tool_powershell("Remove-Item -Recurse -Force C:\\", 60000, None))
    check("powershell destructive blocked at entry",
          r.startswith("Error: 命令被阻止执行"), f"got: {r[:80]}")

    r = asyncio.run(ex._tool_powershell("Format-Volume -DriveLetter C", 60000, None))
    check("powershell Format-Volume blocked at entry",
          r.startswith("Error: 命令被阻止执行"), f"got: {r[:80]}")

    # ================= Static guards =================
    with open(os.path.join(ROOT, "agent_core", "tool_impl", "execution.py"), "r", encoding="utf-8") as f:
        src = f.read()
    check("static: PowerShellValidator wired into powershell tool",
          "PowerShellValidator(self._current_project_path)" in src)
    check("static: destructive scan wired into execute_code",
          "scan_code_for_destructive_behavior(code, language)" in src)
    check("static: BashValidator wired into execute_code bash branch",
          'language == "bash"' in src and "BashValidator(self._current_project_path)" in src)

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
