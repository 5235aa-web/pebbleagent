#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Gateway facade: every srv.<name> used by rpc handlers exists in gateway.server.

The rpc layer reaches the server module through ctx.srv (srv = ctx.srv), so
names imported in gateway/server.py for that purpose are invisible to ruff.
This guard parses both sides and fails when a facade name disappears.

Run: python tests/test_gateway_facade.py   (exit 0 = pass)
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RPC_DIR = os.path.join(ROOT, "gateway", "rpc")
SERVER = os.path.join(ROOT, "gateway", "server.py")

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


def server_bindings():
    with open(SERVER, encoding="utf-8-sig") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def srv_attrs():
    used = {}
    for filename in sorted(os.listdir(RPC_DIR)):
        if not filename.endswith(".py"):
            continue
        path = os.path.join(RPC_DIR, filename)
        with open(path, encoding="utf-8-sig") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "srv"
            ):
                used.setdefault(node.attr, set()).add(filename)
    return used


def main():
    bindings = server_bindings()
    used = srv_attrs()

    check("server module has bindings", len(bindings) > 20, len(bindings))
    check("rpc handlers use srv attributes", len(used) > 20, len(used))

    missing = {name: sorted(files) for name, files in used.items() if name not in bindings}
    detail = "; ".join(f"{name} <- {files}" for name, files in sorted(missing.items()))
    check("every srv attribute is defined in gateway/server.py", not missing, detail)

    facade = {
        "load_tools_info",
        "load_skills_info",
        "current_pricing_period",
        "get_cn_holidays_payload",
        "list_available_agents_from_yaml",
    }
    check("rpc facade names present", facade <= bindings, sorted(facade - bindings))

    probe = {name: files for name, files in used.items() if name not in (bindings - {"load_skills_info"})}
    check("guard detects a removed facade name", "load_skills_info" in probe, sorted(probe)[:5])

    print(f"\nRESULT: PASS={passed} FAIL={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
