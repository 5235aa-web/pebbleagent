#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prompt/YAML contract for code_agent.

Guards four things that silently rot:
  1. Required prompt sections still exist.
  2. Every tool the prompt names is actually callable by code_agent
     (YAML allowlist or an MCP server registered in .mcp.json).
  3. The language policy does not force a single output language.
  4. Severity wording is unambiguous: no "(must)" / "(mandatory rule)" /
     "(important!)" / "(violation = failure)" variants survive, and the
     [MUST] heading count stays at 13.

Run: python tests/test_code_agent_prompt_contract.py   (exit 0 = pass)
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROMPT_PATH = os.path.join(ROOT, "agent_core", "agents", "code_agent_prompt.md")
MCP_CONFIG_PATH = os.path.join(ROOT, ".mcp.json")

# Sections the prompt must keep; deleting one removes a guardrail.
REQUIRED_SECTIONS = [
    "CKB Mechanism",
    "On-demand reading principle",
    "Exemptions from the CKB flow",
    "Build-mode hard constraints",
    "Build fallback when the graph tools are unavailable",
    "No budget-driven sampling",
    "Synchronization duty",
    "Restriction Order",
    "Supreme principle when modifying code",
    "Tool-name rules",
    "Language policy",
    "Life-or-death rules",
    "Tool-calling rules",
    "Tool-name integrity rules",
    "Output-execution consistency iron law",
    "Iteration-completion iron law",
    "Truthfulness iron law",
    "File reading rules",
    "Failure-retry rules",
    "File-operation tasks",
    "Code-editing rules",
    "Core responsibilities",
    "Requirement clarification",
    "Requirement review",
    "Handoff document",
    "When LSP is unavailable",
]

# Lowercase backticked identifiers that are ordinary words, not tool names.
NON_TOOL_TOKENS = {
    "mcp__",  # the prefix itself, named in the tool-name integrity rule
    # todo_write status values
    "pending", "in_progress", "completed",
    # experience_write / experience_update parameter values
    "scope", "project", "global", "rationale", "status",
}

TOOL_TOKEN_RE = re.compile(r"`([a-z][a-z0-9_]{2,})`")
WILDCARD_RE = re.compile(r"^([a-z][a-z0-9_]*)_\*$")


def read_text(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def scan_tool_names_static():
    """Tool names declared in tools/*_tool*.py, without importing the modules.

    Importing them pulls in heavy optional deps (OCR, torch), which the CI
    dependency set deliberately omits, so the static scan is the reliable floor.
    """
    names = set()
    tools_dir = os.path.join(ROOT, "tools")
    if not os.path.isdir(tools_dir):
        return names
    for fn in os.listdir(tools_dir):
        if not (fn.endswith("_tool.py") or fn.endswith("_tools.py")):
            continue
        try:
            text = read_text(os.path.join(tools_dir, fn))
        except Exception:
            continue
        names |= set(re.findall(r'^\s*name\s*=\s*"([a-z][a-z0-9_]*)"', text, re.MULTILINE))
    return names


def mcp_prefixes():
    """mcp__<server>__ for every server in .mcp.json ('-' normalises to '_')."""
    try:
        with io.open(MCP_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        print(f"[WARN] cannot read .mcp.json: {exc}")
        return set()
    servers = (cfg.get("mcpServers") or {}).keys()
    return {"mcp__" + s.replace("-", "_") + "__" for s in servers}


def classify(token, base_names, prefixes):
    """ok | unknown-wildcard | unknown-mcp | suspect"""
    if token in base_names:
        return "ok"
    m = WILDCARD_RE.match(token)
    if m:
        stem = m.group(1) + "_"
        if any(n.startswith(stem) for n in base_names):
            return "ok"
        if any(n.startswith(stem) for n in prefixes):
            return "ok"
        return "unknown-wildcard"
    if token.startswith("mcp__"):
        return "ok" if any(token.startswith(p) for p in prefixes) else "unknown-mcp"
    return "suspect"


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

    from agent_core.agent_loader import load_agent_yaml, clear_agent_cache
    from agent_core.tool_definitions import get_tools_definition

    if not os.path.exists(PROMPT_PATH):
        print(f"[FAIL] prompt missing: {PROMPT_PATH}")
        return 1
    prompt = read_text(PROMPT_PATH)

    clear_agent_cache()
    defn = load_agent_yaml("code_agent")
    check("code_agent.yaml loads", defn is not None)
    allowed = set(defn.tools.allowed) if defn else set()
    check("code_agent allowlist non-empty", bool(allowed), f"got {len(allowed)}")

    # ---------- 1. required sections ----------
    for section in REQUIRED_SECTIONS:
        check(f"section present: {section}", section in prompt)

    # ---------- 2. tool-name sync ----------
    # Universe = static tool declarations + the static schema set
    # (agent_core/tool_definitions.py) + the runtime registry when it imports
    # cleanly. No single source is complete: the schema set misses
    # execute_python / notebook_edit, the registry misses the LSP family.
    base_names = {t["function"]["name"] for t in get_tools_definition()}
    base_names |= scan_tool_names_static()
    if not os.environ.get("CONTRACT_NO_REGISTRY"):
        try:
            from tools.tool_registry import ToolRegistry, auto_register_tools
            auto_register_tools()
            base_names |= set(ToolRegistry.list_tools())
        except Exception as exc:
            print(f"[WARN] tool registry unavailable, static scan only: {exc}")
    prefixes = mcp_prefixes()
    print(f"[INFO] base tools: {len(base_names)} | MCP prefixes: {len(prefixes)}")

    unknown_mcp = sorted({t for t in set(TOOL_TOKEN_RE.findall(prompt)) - NON_TOOL_TOKENS
                          if t.startswith("mcp__")
                          and classify(t, base_names, prefixes) == "unknown-mcp"})
    check("no reference to unregistered MCP server", not unknown_mcp, f"got {unknown_mcp}")

    unknown_wild = sorted({t for t in set(TOOL_TOKEN_RE.findall(prompt)) - NON_TOOL_TOKENS
                           if classify(t, base_names, prefixes) == "unknown-wildcard"})
    check("no dead wildcard tool pattern", not unknown_wild, f"got {unknown_wild}")

    suspects = sorted({t for t in set(TOOL_TOKEN_RE.findall(prompt)) - NON_TOOL_TOKENS
                       if classify(t, base_names, prefixes) == "suspect"})
    if suspects and os.environ.get("CONTRACT_DUMP"):
        dump_path = os.path.join(ROOT, "contract_unknown_tokens.txt")
        with io.open(dump_path, "w", encoding="utf-8") as f:
            f.write("\n".join(suspects))
        print(f"[INFO] dumped {len(suspects)} unknown tokens to {dump_path}")
    check(
        "every snake_case token is a real tool",
        not suspects,
        f"unknown tokens (add to NON_TOOL_TOKENS if they are prose): {suspects}",
    )

    # tools the prompt orders the agent to use must be in the allowlist
    must_allow = [
        "read_file", "write_file", "edit_file", "glob", "grep",
        "search_files", "execute_shell", "web_search", "list_tools",
        "clarify", "load_skill",
    ]
    missing = [t for t in must_allow if t not in allowed]
    check("prompt-critical tools are allowed by YAML", not missing, f"missing {missing}")

    # and every allowlist entry must exist in the registry
    ghost = sorted(t for t in allowed if t not in base_names)
    check("no ghost tool in YAML allowlist", not ghost, f"got {ghost}")

    # stricter direction: a base tool the prompt tells the agent to use must be
    # granted by the YAML allowlist, otherwise the instruction is unexecutable
    prompt_tools = {
        t for t in set(TOOL_TOKEN_RE.findall(prompt)) - NON_TOOL_TOKENS
        if not t.startswith("mcp__")
        and not WILDCARD_RE.match(t)
        and t in base_names
    }
    not_allowed = sorted(prompt_tools - allowed)
    check("every base tool named in prompt is allowed by YAML", not not_allowed,
          f"prompt names but YAML withholds: {not_allowed}")

    # ---------- 3. language policy ----------
    forbidden_language_rules = [
        "entirely in English",
        "Never output non-English",
        "must think and output entirely",
    ]
    hits = [s for s in forbidden_language_rules if s.lower() in prompt.lower()]
    check("no forced single-language rule", not hits, f"found {hits}")
    check("language policy states user-language matching",
          "language the user writes in" in prompt.lower())

    # ---------- 4. severity budget ----------
    ambiguous = re.findall(
        r"\((?:must|mandatory rules?|important!|violation = failure)\)", prompt
    )
    check("no ambiguous severity markers", not ambiguous, f"got {ambiguous}")

    must_headings = re.findall(r"^#{2,4} .*\[MUST\]\s*$", prompt, re.MULTILINE)
    check("[MUST] heading count is 13", len(must_headings) == 13, f"got {len(must_headings)}")
    check("truthfulness is [MUST]", any("Truthfulness" in m for m in must_headings))
    check("output-execution is [MUST]",
          any("Output-execution consistency" in m for m in must_headings))

    # ---------- 5. build-mode guard is not absolutist ----------
    check("CKB exemptions exist", "Exemptions from the CKB flow" in prompt)
    check("MCP fallback documented", "Build fallback when the graph tools are unavailable" in prompt)
    check("no unconditional 'absolutely forbidden' in build mode",
          "it is absolutely forbidden to" not in prompt)
    check("retry has a time budget", "60 seconds" in prompt)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
